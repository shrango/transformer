#encoding: utf-8

import torch

from torch.optim import Adam as Optimizer

from parallel.base import DataParallelCriterion
from parallel.parallelMT import DataParallelMT
from parallel.optm import MultiGPUGradScaler

from utils.base import *
from utils.init.base import init_model_params
from utils.contpara import get_model_parameters
from utils.state.holder import Holder
from utils.state.pyrand import PyRandomState
from utils.state.thrand import THRandomState
from utils.fmt.base import tostr
from cnfg.vocab.base import pad_id
from utils.fmt.base4torch import parse_cuda, load_emb
from utils.mulang import data_sampler

from lrsch import GoogleLR as LRScheduler
from loss.base import MultiLabelSmoothingLoss as LabelSmoothingLoss

from random import shuffle, randint

from utils.tqdm import tqdm

from utils.h5serial import h5File

import cnfg.mulang as cnfg
from cnfg.ihyp import *

from transformer.MuLang.NMT import NMT

def back_translate(model, seq_in, taskid, beam_size, multi_gpu, enable_autocast=False, step_bsize=32, step_ntok=640, pivot_bt=True):

	rs = []
	bsize, seql = seq_in.size()
	_step_bsize = min(step_bsize, step_ntok // seql)
	_max_len = min(255, seql + 16)
	if multi_gpu:
		_g_out = model.gather_output
		model.gather_output = True
	sind = 0
	with torch.no_grad(), autocast(enabled=enable_autocast):
		while sind < bsize:
			num_narrow = min(_step_bsize, bsize - sind)
			if pivot_bt and (taskid != 0):
				out = model.decode(seq_in.narrow(0, sind, num_narrow), taskid=0, beam_size=beam_size, max_len=_max_len).detach()
				rs.append(model.decode(torch.cat((torch.ones(num_narrow, 1, dtype=out.dtype, device=out.device), out,), dim=1), taskid=taskid, beam_size=beam_size, max_len=_max_len).detach())
				out = _tid = None
			else:
				rs.append(model.decode(seq_in.narrow(0, sind, num_narrow), taskid=taskid, beam_size=beam_size, max_len=_max_len).detach())
			sind += num_narrow
	if multi_gpu:
		model.gather_output = _g_out
	rs = torch.cat(pad_tensors(rs), dim=0)
	rs = torch.cat((torch.ones(bsize, 1, dtype=rs.dtype, device=rs.device), rs,), dim=1)

	return rs

def train(td, tl, ed, nd, optm, lrsch, model, lossf, mv_device, logger, done_tokens, multi_gpu, multi_gpu_optimizer, tokens_optm=32768, nreport=None, save_every=None, chkpf=None, state_holder=None, statesf=None, num_checkpoint=1, cur_checkid=0, report_eva=True, remain_steps=None, save_loss=False, save_checkp_epoch=False, scaler=None):

	sum_loss = part_loss = 0.0
	sum_wd = part_wd = 0
	_done_tokens, _cur_checkid, _cur_rstep, _use_amp = done_tokens, cur_checkid, remain_steps, scaler is not None
	model.train()
	cur_b, _ls = 1, {} if save_loss else None
	global ntask, ro_beam_size
	t_sample_max_id = ntask - 2
	for i_d, taskid in tqdm(tl, mininterval=tqdm_mininterval):
		seq_o = torch.from_numpy(td[str(taskid)]["tgt"][i_d][()])
		lo = seq_o.size(1) - 1
		if mv_device:
			seq_o = seq_o.to(mv_device, non_blocking=True)
		seq_o = seq_o.long()

		_bt_taskid = randint(0, t_sample_max_id)
		if _bt_taskid >= taskid:
			_bt_taskid += 1
		seq_batch = back_translate(model, seq_o, _bt_taskid, ro_beam_size, multi_gpu, enable_autocast=_use_amp)
		oi = seq_o.narrow(1, 0, lo)
		ot = seq_o.narrow(1, 1, lo).contiguous()
		with autocast(enabled=_use_amp):
			output = model(seq_batch, oi, taskid=taskid)
			loss = lossf(output, ot, lang_id=taskid)
			if multi_gpu:
				loss = loss.sum()
		loss_add = loss.data.item()

		if scaler is None:
			loss.backward()
		else:
			scaler.scale(loss).backward()

		wd_add = ot.ne(pad_id).int().sum().item()
		loss = output = oi = ot = seq_batch = seq_o = None
		sum_loss += loss_add
		if save_loss:
			_ls[(i_d, t_d)] = loss_add / wd_add
		sum_wd += wd_add
		_done_tokens += wd_add

		if _done_tokens >= tokens_optm:
			optm_step(optm, model=model, scaler=scaler, multi_gpu=multi_gpu, multi_gpu_optimizer=multi_gpu_optimizer, zero_grad_none=optm_step_zero_grad_set_none)
			_done_tokens = 0
			if _cur_rstep is not None:
				if save_checkp_epoch and (save_every is not None) and (_cur_rstep % save_every == 0) and (chkpf is not None) and (_cur_rstep > 0):
					if num_checkpoint > 1:
						_fend = "_%d.h5" % (_cur_checkid)
						_chkpf = chkpf[:-3] + _fend
						_cur_checkid = (_cur_checkid + 1) % num_checkpoint
					else:
						_chkpf = chkpf
					save_model(model, _chkpf, multi_gpu, print_func=logger.info)
					if statesf is not None:
						save_states(state_holder.state_dict(update=False, **{"remain_steps": _cur_rstep, "checkpoint_id": _cur_checkid, "training_list": tl[cur_b - 1:]}), statesf, print_func=logger.info)
				_cur_rstep -= 1
				if _cur_rstep <= 0:
					break
			lrsch.step()

		if nreport is not None:
			part_loss += loss_add
			part_wd += wd_add
			if cur_b % nreport == 0:
				if report_eva:
					_leva, _eeva = eva(ed, nd, model, lossf, mv_device, multi_gpu, _use_amp)
					logger.info("Average loss over %d tokens: %.3f, valid loss/error: %.3f %.2f" % (part_wd, part_loss / part_wd, _leva, _eeva,))
					free_cache(mv_device)
					model.train()
				else:
					logger.info("Average loss over %d tokens: %.3f" % (part_wd, part_loss / part_wd,))
				part_loss = 0.0
				part_wd = 0

		if save_checkp_epoch and (_cur_rstep is None) and (save_every is not None) and (cur_b % save_every == 0) and (chkpf is not None) and (cur_b < ndata):
			if num_checkpoint > 1:
				_fend = "_%d.h5" % (_cur_checkid)
				_chkpf = chkpf[:-3] + _fend
				_cur_checkid = (_cur_checkid + 1) % num_checkpoint
			else:
				_chkpf = chkpf
			save_model(model, _chkpf, multi_gpu, print_func=logger.info)
			if statesf is not None:
				save_states(state_holder.state_dict(update=False, **{"remain_steps": _cur_rstep, "checkpoint_id": _cur_checkid, "training_list": tl[cur_b - 1:]}), statesf, print_func=logger.info)
		cur_b += 1
	if part_wd != 0.0:
		logger.info("Average loss over %d tokens: %.3f" % (part_wd, part_loss / part_wd,))
	return sum_loss / sum_wd, _done_tokens, _cur_checkid, _cur_rstep, _ls

def eva(ed, nd, model, lossf, mv_device, multi_gpu, use_amp=False):
	r = w = 0
	sum_loss = 0.0
	model.eval()
	with torch.no_grad():
		for i_d, taskid in tqdm(nd, mininterval=tqdm_mininterval):
			task_grp = ed[str(taskid)]
			seq_batch = torch.from_numpy(task_grp["src"][i_d][()])
			seq_o = torch.from_numpy(task_grp["tgt"][i_d][()])
			lo = seq_o.size(1) - 1
			if mv_device:
				seq_batch = seq_batch.to(mv_device, non_blocking=True)
				seq_o = seq_o.to(mv_device, non_blocking=True)
			seq_batch, seq_o = seq_batch.long(), seq_o.long()
			ot = seq_o.narrow(1, 1, lo).contiguous()
			with autocast(enabled=use_amp):
				output = model(seq_batch, seq_o.narrow(1, 0, lo), taskid=taskid)
				loss = lossf(output, ot, lang_id=taskid)
				if multi_gpu:
					loss = loss.sum()
					trans = torch.cat([outu.argmax(-1).to(mv_device, non_blocking=True) for outu in output], 0)
				else:
					trans = output.argmax(-1)
			sum_loss += loss.data.item()
			data_mask = ot.ne(pad_id)
			correct = (trans.eq(ot) & data_mask).int()
			w += data_mask.int().sum().item()
			r += correct.sum().item()
			correct = data_mask = trans = loss = output = ot = seq_batch = seq_o = None
	w = float(w)
	return sum_loss / w, (w - r) / w * 100.0

def hook_lr_update(optm, flags=None):

	reset_Adam(optm, flags)

def init_fixing(module):

	if hasattr(module, "fix_init"):
		module.fix_init()

def load_fixing(module):

	if hasattr(module, "fix_load"):
		module.fix_load()

rid = cnfg.run_id
earlystop = cnfg.earlystop
maxrun = cnfg.maxrun
tokens_optm = cnfg.tokens_optm
done_tokens = 0
batch_report = cnfg.batch_report
report_eva = cnfg.report_eva
use_ams = cnfg.use_ams
cnt_states = cnfg.train_statesf
save_auto_clean = cnfg.save_auto_clean
overwrite_eva = cnfg.overwrite_eva
save_every = cnfg.save_every
start_chkp_save = cnfg.epoch_start_checkpoint_save
epoch_save = cnfg.epoch_save
remain_steps = cnfg.training_steps
ro_beam_size = cnfg.robt_beam_size

wkdir = "".join((cnfg.exp_dir, cnfg.data_id, "/", cnfg.group_id, "/", rid, "/"))
mkdir(wkdir)

chkpf = None
statesf = None
if save_every is not None:
	chkpf = wkdir + "checkpoint.h5"
if cnfg.save_train_state:
	statesf = wkdir + "train.states.t7"

logger = get_logger(wkdir + "train.log")

use_cuda, cuda_device, cuda_devices, multi_gpu = parse_cuda(cnfg.use_cuda, cnfg.gpuid)
multi_gpu_optimizer = multi_gpu and cnfg.multi_gpu_optimizer

set_random_seed(cnfg.seed, use_cuda)

td = h5File(cnfg.train_data, "r")
vd = h5File(cnfg.dev_data, "r")

ntrain = td["ndata"][()].tolist()
nvalid = vd["ndata"][()].tolist()
nword = td["nword"][()].tolist()
nwordi, ntask, nwordt = nword[0], nword[1], nword[-1]

task_weight, task_weight_T = cnfg.task_weight, cnfg.task_weight_T
if task_weight_T is None or task_weight_T == 1.0:
	tl = [(str(i), _task,) for _nd, _task in zip(ntrain, td["taskorder"][()].tolist()) for i in range(_nd)]
	train_sampler = None
else:
	train_taskorder = td["taskorder"][()].tolist()
	_tnd = dict(zip(train_taskorder, ntrain))
	train_taskorder.sort()
	ntrain = [_tnd[i] for i in train_taskorder]
	_tnd = None
	train_sampler = data_sampler(ntrain if task_weight is None else task_weight, task_weight_T, ntrain, train_taskorder, nsample=sum(ntrain))
nvalid = [(str(i), _task,) for _nd, _task in zip(nvalid, vd["taskorder"][()].tolist()) for i in range(_nd)]

logger.info("Design models with seed: %d" % torch.initial_seed())
mymodel = NMT(cnfg.isize, nwordi, nwordt, cnfg.nlayer, cnfg.ff_hsize, cnfg.drop, cnfg.attn_drop, cnfg.share_emb, cnfg.nhead, cache_len_default, cnfg.attn_hsize, cnfg.norm_output, cnfg.bindDecoderEmb, cnfg.forbidden_indexes, ntask=ntask, ngroup=cnfg.ngroup)

fine_tune_m = cnfg.fine_tune_m

mymodel = init_model_params(mymodel)
mymodel.apply(init_fixing)
if fine_tune_m is not None:
	logger.info("Load pre-trained model from: " + fine_tune_m)
	mymodel = load_model_cpu(fine_tune_m, mymodel)
	mymodel.apply(load_fixing)

lossf = LabelSmoothingLoss(nwordt, cnfg.label_smoothing, ignore_index=pad_id, reduction="sum", forbidden_index=cnfg.forbidden_indexes)

if cnfg.src_emb is not None:
	logger.info("Load source embedding from: " + cnfg.src_emb)
	load_emb(cnfg.src_emb, mymodel.enc.wemb.weight, nwordi, cnfg.scale_down_emb, cnfg.freeze_srcemb)
if cnfg.tgt_emb is not None:
	logger.info("Load target embedding from: " + cnfg.tgt_emb)
	load_emb(cnfg.tgt_emb, mymodel.dec.wemb.weight, nwordt, cnfg.scale_down_emb, cnfg.freeze_tgtemb)

if cuda_device:
	mymodel.to(cuda_device, non_blocking=True)
	lossf.to(cuda_device, non_blocking=True)

use_amp = cnfg.use_amp and use_cuda
scaler = (MultiGPUGradScaler() if multi_gpu_optimizer else GradScaler()) if use_amp else None

if multi_gpu:
	mymodel = DataParallelMT(mymodel, device_ids=cuda_devices, output_device=cuda_device.index, host_replicate=True, gather_output=False)
	lossf = DataParallelCriterion(lossf, device_ids=cuda_devices, output_device=cuda_device.index, replicate_once=True)

if multi_gpu:
	optimizer = mymodel.build_optimizer(Optimizer, lr=init_lr, betas=adam_betas_default, eps=ieps_adam_default, weight_decay=cnfg.weight_decay, amsgrad=use_ams, multi_gpu_optimizer=multi_gpu_optimizer, contiguous_parameters=contiguous_parameters)
else:
	optimizer = Optimizer(get_model_parameters(mymodel, contiguous_parameters=contiguous_parameters), lr=init_lr, betas=adam_betas_default, eps=ieps_adam_default, weight_decay=cnfg.weight_decay, amsgrad=use_ams)
optimizer.zero_grad(set_to_none=optm_step_zero_grad_set_none)

lrsch = LRScheduler(optimizer, cnfg.isize, cnfg.warm_step, scale=cnfg.lr_scale)

state_holder = None if statesf is None and cnt_states is None else Holder(**{"optm": optimizer, "lrsch": lrsch, "pyrand": PyRandomState(), "thrand": THRandomState(use_cuda=use_cuda)})

num_checkpoint = cnfg.num_checkpoint
cur_checkid = 0

tminerr = inf_default

minloss, minerr = eva(vd, nvalid, mymodel, lossf, cuda_device, multi_gpu, use_amp)
logger.info("Init lr: %s, Dev Loss/Error: %.3f %.2f" % (" ".join(tostr(getlr(optimizer))), minloss, minerr,))

if fine_tune_m is None:
	save_model(mymodel, wkdir + "init.h5", multi_gpu, print_func=logger.info)
	logger.info("Initial model saved")
else:
	if cnt_states is not None:
		logger.info("Loading training states")
		_remain_states = state_holder.load_state_dict(torch.load(cnt_states))
		remain_steps, cur_checkid = _remain_states["remain_steps"], _remain_states["checkpoint_id"]
		if "training_list" in _remain_states:
			_ctl = _remain_states["training_list"]
		else:
			shuffle(tl)
			_ctl = tl
		tminerr, done_tokens, cur_checkid, remain_steps, _ = train(td, _ctl, vd, nvalid, optimizer, lrsch, mymodel, lossf, cuda_device, logger, done_tokens, multi_gpu, multi_gpu_optimizer, tokens_optm, batch_report, save_every, chkpf, state_holder, statesf, num_checkpoint, cur_checkid, report_eva, remain_steps, False, False, scaler)
		_ctl = _remain_states = None
		vloss, vprec = eva(vd, nvalid, mymodel, lossf, cuda_device, multi_gpu, use_amp)
		logger.info("Epoch: 0, train loss: %.3f, valid loss/error: %.3f %.2f" % (tminerr, vloss, vprec,))
		save_model(mymodel, wkdir + "train_0_%.3f_%.3f_%.2f.h5" % (tminerr, vloss, vprec,), multi_gpu, print_func=logger.info, mtyp=("eva" if overwrite_eva else "train") if save_auto_clean else None)
		if statesf is not None:
			save_states(state_holder.state_dict(update=False, **{"remain_steps": remain_steps, "checkpoint_id": cur_checkid}), statesf, print_func=logger.info)
		logger.info("New best model saved")

if cnfg.dss_ws is not None and cnfg.dss_ws > 0.0 and cnfg.dss_ws < 1.0:
	dss_ws = int(cnfg.dss_ws * sum(ntrain))
	_Dws = {}
	_prev_Dws = {}
	_crit_inc = {}
	if cnfg.dss_rm is not None and cnfg.dss_rm > 0.0 and cnfg.dss_rm < 1.0:
		dss_rm = int(cnfg.dss_rm * sum(ntrain) * (1.0 - cnfg.dss_ws))
	else:
		dss_rm = 0
else:
	dss_ws = 0
	dss_rm = 0
	_Dws = None

namin = 0

for i in range(1, maxrun + 1):
	if train_sampler is None:
		shuffle(tl)
	else:
		tl = train_sampler.generate()
	free_cache(use_cuda)
	terr, done_tokens, cur_checkid, remain_steps, _Dws = train(td, tl, vd, nvalid, optimizer, lrsch, mymodel, lossf, cuda_device, logger, done_tokens, multi_gpu, multi_gpu_optimizer, tokens_optm, batch_report, save_every, chkpf, state_holder, statesf, num_checkpoint, cur_checkid, report_eva, remain_steps, dss_ws > 0, i >= start_chkp_save, scaler)
	vloss, vprec = eva(vd, nvalid, mymodel, lossf, cuda_device, multi_gpu, use_amp)
	logger.info("Epoch: %d, train loss: %.3f, valid loss/error: %.3f %.2f" % (i, terr, vloss, vprec,))

	if (vprec <= minerr) or (vloss <= minloss):
		save_model(mymodel, wkdir + "eva_%d_%.3f_%.3f_%.2f.h5" % (i, terr, vloss, vprec,), multi_gpu, print_func=logger.info, mtyp="eva" if save_auto_clean else None)
		if statesf is not None:
			save_states(state_holder.state_dict(update=False, **{"remain_steps": remain_steps, "checkpoint_id": cur_checkid}), statesf, print_func=logger.info)
		logger.info("New best model saved")

		namin = 0

		if vprec < minerr:
			minerr = vprec
		if vloss < minloss:
			minloss = vloss

	else:
		if terr < tminerr:
			tminerr = terr
			save_model(mymodel, wkdir + "train_%d_%.3f_%.3f_%.2f.h5" % (i, terr, vloss, vprec,), multi_gpu, print_func=logger.info, mtyp=("eva" if overwrite_eva else "train") if save_auto_clean else None)
			if statesf is not None:
				save_states(state_holder.state_dict(update=False, **{"remain_steps": remain_steps, "checkpoint_id": cur_checkid}), statesf, print_func=logger.info)
		elif epoch_save:
			save_model(mymodel, wkdir + "epoch_%d_%.3f_%.3f_%.2f.h5" % (i, terr, vloss, vprec,), multi_gpu, print_func=logger.info)
			if statesf is not None:
				save_states(state_holder.state_dict(update=False, **{"remain_steps": remain_steps, "checkpoint_id": cur_checkid}), statesf, print_func=logger.info)

		namin += 1
		if namin >= earlystop:
			if done_tokens > 0:
				optm_step(optimizer, model=mymodel, scaler=scaler, multi_gpu=multi_gpu, multi_gpu_optimizer=multi_gpu_optimizer)
				lrsch.step()
				done_tokens = 0
			logger.info("early stop")
			break

	if remain_steps is not None and remain_steps <= 0:
		logger.info("Last training step reached")
		break

	if dss_ws > 0:
		if _prev_Dws and (train_sampler is None):
			for _key, _value in _Dws.items():
				if _key in _prev_Dws:
					_ploss = _prev_Dws[_key]
					_crit_inc[_key] = (_ploss - _value) / _ploss
			tl = dynamic_sample(_crit_inc, dss_ws, dss_rm)
		_prev_Dws = _Dws

if done_tokens > 0:
	optm_step(optimizer, model=mymodel, scaler=scaler, multi_gpu=multi_gpu, multi_gpu_optimizer=multi_gpu_optimizer)
	lrsch.step()

save_model(mymodel, wkdir + "last.h5", multi_gpu, print_func=logger.info)
if statesf is not None:
	save_states(state_holder.state_dict(update=False, **{"remain_steps": remain_steps, "checkpoint_id": cur_checkid}), statesf, print_func=logger.info)
logger.info("model saved")

td.close()
vd.close()
