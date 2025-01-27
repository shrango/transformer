#encoding: utf-8

import torch
from torch import Tensor
from torch.nn import ModuleDict
from os import makedirs, remove
from os.path import exists as fs_check
from threading import Thread
from functools import wraps
from random import sample, seed as rpyseed
from math import ceil

import logging

from utils.h5serial import h5save, h5load

from cnfg.ihyp import h5modelwargs, optm_step_zero_grad_set_none, n_keep_best, use_deterministic, enable_torch_check

try:
	torch.autograd.set_detect_anomaly(enable_torch_check)
except Exception as e:
	print(e)

secure_type_map = {torch.float16: torch.float64, torch.float32: torch.float64, torch.uint8: torch.int64, torch.int8: torch.int64, torch.int16: torch.int64, torch.int32: torch.int64}

def all_done_bool(stat, *inputs, **kwargs):

	return stat.all().item()

def all_done_byte(stat, bsize=None, **kwargs):

	return stat.int().sum().item() == (stat.numel() if bsize is None else bsize)

def exist_any_bool(stat):

	return stat.any().item()

def exist_any_byte(stat):

	return stat.int().sum().item() > 0

def torch_all_bool_wodim(x, *inputs, **kwargs):

	return x.all(*inputs, **kwargs)

def torch_all_byte_wodim(x, *inputs, **kwargs):

	return x.int().sum(*inputs, **kwargs).eq(x.numel())

def torch_all_bool_dim(x, dim, *inputs, **kwargs):

	return x.all(dim, *inputs, **kwargs)

def torch_all_byte_dim(x, dim, *inputs, **kwargs):

	return x.int().sum(*inputs, dim=dim, **kwargs).eq(x.size(dim))

def torch_all_bool(x, *inputs, dim=None, **kwargs):

	return x.all(*inputs, **kwargs) if dim is None else x.all(dim, *inputs, **kwargs)

def torch_all_byte(x, *inputs, dim=None, **kwargs):

	return x.int().sum(*inputs, **kwargs).eq(x.numel()) if dim is None else x.int().sum(*inputs, dim=dim, **kwargs).eq(x.size(dim))

def torch_any_bool_wodim(x, *inputs, **kwargs):

	return x.any(*inputs, **kwargs)

def torch_any_byte_wodim(x, *inputs, **kwargs):

	return x.int().sum(*inputs, **kwargs).gt(0)

def torch_any_bool_dim(x, dim, *inputs, **kwargs):

	return x.any(dim, *inputs, **kwargs)

def torch_any_byte_dim(x, dim, *inputs, **kwargs):

	return x.int().sum(*inputs, dim=dim, **kwargs).gt(0)

def torch_any_bool(x, *inputs, dim=None, **kwargs):

	return x.any(*inputs, **kwargs) if dim is None else x.any(dim, *inputs, **kwargs)

def torch_any_byte(x, *inputs, dim=None, **kwargs):

	return x.int().sum(*inputs, **kwargs).gt(0) if dim is None else x.int().sum(*inputs, dim=dim, **kwargs).gt(0)

def flip_mask_bool(mask, dim):

	return mask.to(torch.uint8, non_blocking=True).flip(dim).to(mask.dtype, non_blocking=True)

def flip_mask_byte(mask, dim):

	return mask.flip(dim)

class EmptyAutocast:

	def __init__(self, *inputs, **kwargs):

		self.args, self.kwargs = inputs, kwargs

	def __enter__(self):

		return self

	def __exit__(self, *inputs, **kwargs):

		pass

class EmptyGradScaler:

	def __init__(self, *args, **kwargs):

		self.args, self.kwargs = args, kwargs

	def scale(self, outputs):

		return outputs

	def step(self, optimizer, *args, **kwargs):

		return optimizer.step(*args, **kwargs)

	def update(self, *args, **kwargs):

		pass

def is_autocast_enabled_empty(*args, **kwargs):

	return False

# handling torch.bool
try:
	mask_tensor_type = torch.bool
	secure_type_map[mask_tensor_type] = torch.int64
	nccl_type_map = {torch.bool:torch.uint8}
	all_done = all_done_bool
	exist_any = exist_any_bool
	torch_all = torch_all_bool
	torch_all_dim = torch_all_bool_dim
	torch_all_wodim = torch_all_bool_wodim
	torch_any = torch_any_bool
	torch_any_dim = torch_any_bool_dim
	torch_any_wodim = torch_any_bool_wodim
	flip_mask = flip_mask_bool
except Exception as e:
	mask_tensor_type = torch.uint8
	nccl_type_map = None
	all_done = all_done_byte
	exist_any = exist_any_byte
	torch_all = torch_all_byte
	torch_all_dim = torch_all_byte_dim
	torch_all_wodim = torch_all_byte_wodim
	torch_any = torch_any_byte
	torch_any_dim = torch_any_byte_dim
	torch_any_wodim = torch_any_byte_wodim
	flip_mask = flip_mask_byte

# handling torch.cuda.amp, fp16 will NOT be really enabled if torch.cuda.amp does not exist (for early versions)
try:
	from torch.cuda.amp import autocast, GradScaler
	is_autocast_enabled = torch.is_autocast_enabled
	fp16_supported = True
except Exception as e:
	autocast, GradScaler, is_autocast_enabled, fp16_supported = EmptyAutocast, EmptyGradScaler, is_autocast_enabled_empty, False

def pad_tensors(tensor_list, dim=-1):

	def get_pad_size(tsize, stdlen, dim=-1):

		nsize = list(tsize)
		nsize[dim] = stdlen - tsize[dim]

		return nsize

	maxlen = 0
	for tensor in tensor_list:
		tlen = tensor.size(dim)
		if tlen > maxlen:
			maxlen = tlen

	return [tensor if tensor.size(dim) == maxlen else torch.cat((tensor, tensor.new_zeros(get_pad_size(tensor.size(), maxlen))), dim) for tensor in tensor_list]

def clear_pad(batch_in, mask=None, dim=-1):

	_mask = batch_in.eq(0) if mask is None else mask
	npad = _mask.int().sum(dim).min().item()
	if npad > 0:
		return batch_in.narrow(dim, 0, batch_in.size(dim) - npad)
	else:
		return batch_in

def clear_pad_mask(batch_list, mask, dims, mask_dim=-1, return_contiguous=True):

	npad = mask.int().sum(mask_dim).min().item()
	if npad > 0:
		_n_ret = mask.size(mask_dim) - npad
		if return_contiguous:
			return [batchu.narrow(dim, 0, _n_ret).contiguous() for batchu, dim in zip(batch_list, dims)], mask.narrow(mask_dim, 0, _n_ret).contiguous()
		else:
			return [batchu.narrow(dim, 0, _n_ret) for batchu, dim in zip(batch_list, dims)], mask.narrow(mask_dim, 0, _n_ret)
	else:
		return batch_list, mask

def freeze_module(module):

	for p in module.parameters():
		if p.requires_grad:
			p.requires_grad_(False)

def unfreeze_module(module):

	def unfreeze_fixing(mod):

		if hasattr(mod, "fix_unfreeze"):
			mod.fix_unfreeze()

	for p in module.parameters():
		p.requires_grad_(True)

	module.apply(unfreeze_fixing)

def eq_indexes(tensor, indexes):

	rs = None
	for ind in indexes:
		if rs is None:
			rs = tensor.eq(ind)
		else:
			rs |= tensor.eq(ind)
	return rs

def getlr(optm):

	lr = []
	for i, param_group in enumerate(optm.param_groups):
		lr.append(float(param_group["lr"]))

	return lr

def updated_lr(oldlr, newlr):

	rs = False
	for olr, nlr in zip(oldlr, newlr):
		if olr != nlr:
			rs = True
			break

	return rs

def reset_Adam(optm, amsgrad=False):

	for group in optm.param_groups:
		for p in group["params"]:
			state = optm.state[p]
			if len(state) != 0:
				state["step"] = 0
				state["exp_avg"].zero_()
				state["exp_avg_sq"].zero_()
				if amsgrad:
					state["max_exp_avg_sq"].zero_()

def reinit_Adam(optm, amsgrad=False):

	for group in optm.param_groups:
		for p in group["params"]:
			optm.state[p].clear()

def dynamic_sample(incd, dss_ws, dss_rm):

	rd = {}
	for k, v in incd.items():
		if v in rd:
			rd[v].append(k)
		else:
			rd[v] = [k]
	incs = list(rd.keys())
	incs.sort(reverse=True)
	_full_rl = []
	for v in incs:
		_full_rl.extend(rd[v])

	return _full_rl[:dss_ws] + sample(_full_rl[dss_ws:], dss_rm) if dss_rm > 0 else _full_rl[:dss_ws]

def load_model_cpu(modf, base_model):

	mpg = h5load(modf)

	for para, mp in zip(base_model.parameters(), mpg):
		para.data = mp.data

	return base_model

def load_model_cpu_old(modf, base_model):

	base_model.load_state_dict(h5load(modf))

	return base_model

class SaveModelCleaner:

	def __init__(self):

		self.holder = {}

	def __call__(self, fname, typename):

		if typename in self.holder:
			self.holder[typename].update(fname)
		else:
			self.holder[typename] = bestfkeeper(fnames=[fname])

save_model_cleaner = SaveModelCleaner()

def save_model(model, fname, sub_module=False, print_func=print, mtyp=None, h5args=h5modelwargs):

	_msave = model.module if sub_module else model
	try:
		h5save([t.data for t in _msave.parameters()], fname, h5args=h5args)
		if mtyp is not None:
			save_model_cleaner(fname, mtyp)
	except Exception as e:
		if print_func is not None:
			print_func(str(e))

def async_save_model(model, fname, sub_module=False, print_func=print, mtyp=None, h5args=h5modelwargs, para_lock=None, log_success=None):

	def _worker(model, fname, sub_module=False, print_func=print, mtyp=None, para_lock=None, log_success=None):

		success = True
		_msave = model.module if sub_module else model
		try:
			if para_lock is None:
				h5save([t.data for t in _msave.parameters()], fname, h5args=h5args)
				if mtyp is not None:
					save_model_cleaner(fname, mtyp)
			else:
				with para_lock:
					h5save([t.data for t in _msave.parameters()], fname, h5args=h5args)
					if mtyp is not None:
						save_model_cleaner(fname, mtyp)
		except Exception as e:
			if print_func is not None:
				print_func(str(e))
			success = False
		if success and (print_func is not None) and (log_success is not None):
			print_func(str(log_success))

	Thread(target=_worker, args=(model, fname, sub_module, print_func, mtyp, para_lock, log_success)).start()

def save_states(state_dict, fname, print_func=print, mtyp=None):

	try:
		torch.save(state_dict, fname)
		if mtyp is not None:
			save_model_cleaner(fname, mtyp)
	except Exception as e:
		if print_func is not None:
			print_func(str(e))

def get_logger(fname):

	logger = logging.getLogger(__name__)
	logger.setLevel(level=logging.INFO)
	handler = logging.FileHandler(fname)
	handler.setLevel(logging.INFO)
	formatter = logging.Formatter("[%(asctime)s %(levelname)s] %(message)s")
	handler.setFormatter(formatter)

	console = logging.StreamHandler()
	console.setLevel(logging.INFO)

	logger.addHandler(handler)
	logger.addHandler(console)

	return logger

def set_random_seed(seed, set_cuda=False):

	_rseed = torch.initial_seed() if seed is None else seed
	rpyseed(_rseed)
	torch.manual_seed(_rseed)
	if set_cuda:
		torch.cuda.manual_seed_all(_rseed)
		try:
			torch.backends.cuda.matmul.allow_tf32 = use_deterministic
			torch.backends.cudnn.allow_tf32 = use_deterministic
			torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = allow_fp16_reduction
		except:
			pass
		# Make cudnn methods deterministic according to: https://pytorch.org/docs/stable/notes/randomness.html#cudnn
		try:
			torch.use_deterministic_algorithms(use_deterministic)
		except:
			torch.backends.cudnn.deterministic = use_deterministic
		torch.backends.cudnn.benchmark = False

def module_train(netin, module, mode=True):

	for net in netin.modules():
		if isinstance(net, module):
			net.train(mode=mode)

	return netin

def repeat_bsize_for_beam_tensor(tin, beam_size):

	_tsize = list(tin.size())
	_rarg = [1 for i in range(len(_tsize))]
	_rarg[1] = beam_size
	_tsize[0] *= beam_size

	return tin.repeat(*_rarg).view(_tsize)

def expand_bsize_for_beam(*inputs, beam_size=1):

	outputs = []
	for inputu in inputs:
		if isinstance(inputu, Tensor):
			outputs.append(repeat_bsize_for_beam_tensor(inputu, beam_size))
		elif isinstance(inputu, dict):
			outputs.append({k: expand_bsize_for_beam(v, beam_size=beam_size) for k, v in inputu.items()})
		elif isinstance(inputu, tuple):
			outputs.append(tuple(expand_bsize_for_beam(tmpu, beam_size=beam_size) for tmpu in inputu))
		elif isinstance(inputu, list):
			outputs.append([expand_bsize_for_beam(tmpu, beam_size=beam_size) for tmpu in inputu])
		else:
			outputs.append(inputu)

	return outputs[0] if len(inputs) == 1 else tuple(outputs)

def index_tensors(*inputs, indices=None, dim=0):

	outputs = []
	for inputu in inputs:
		if isinstance(inputu, Tensor):
			outputs.append(inputu.index_select(dim, indices))
		elif isinstance(inputu, dict):
			outputs.append({k: index_tensors(v, indices=indices, dim=dim) for k, v in inputu.items()})
		elif isinstance(inputu, tuple):
			outputs.append(tuple(index_tensors(tmpu, indices=indices, dim=dim) for tmpu in inputu))
		elif isinstance(inputu, list):
			outputs.append([index_tensors(tmpu, indices=indices, dim=dim) for tmpu in inputu])
		else:
			outputs.append(inputu)

	return outputs[0] if len(inputs) == 1 else tuple(outputs)

def select_zero_(x, dim, index):

	x.select(dim, index).zero_()

	return x

def remove_layers(all_layers, ltr):

	return [_l for i, _l in enumerate(all_layers) if i not in ltr]

def free_cache(free_cuda=False):

	if free_cuda:
		torch.cuda.empty_cache()

def filter_para_grad(plin):

	return [para for para in plin if para.requires_grad]

def filter_para_grad_iter(plin):

	for para in plin:
		if para.requires_grad:
			yield para

def ModuleList2Dict(modin):

	return ModuleDict(zip([str(i) for i in range(len(modin))], modin))

def add_module(m, strin, m_add):

	_name_list = strin.split(".")
	if len(_name_list) == 1:
		m.add_module(strin, m_add)
	else:
		_m = m
		# update _modules with pytorch: https://pytorch.org/docs/stable/_modules/torch/nn/modules/module.html#Module.add_module
		for _tmp in _name_list[:-1]:
			_m = _m._modules[_tmp]
		_m.add_module(_name_list[-1], m_add)

	return m

def reduce_model_core(modin, redm, attr_func=None):

	if attr_func is None:
		_m_sel = None
		for _name, _module in modin.named_modules():
			if isinstance(_module, redm):
				if _m_sel is None:
					_m_sel = _module
				else:
					add_module(modin, _name, _m_sel)
	else:
		_m_sel = {}
		for _name, _module in modin.named_modules():
			if isinstance(_module, redm):
				_key = attr_func(_module)
				if _key in _m_sel:
					add_module(modin, _name, _m_sel[_key])
				else:
					_m_sel[_key] = _module

	return modin

def reduce_model_list(modin, redml, attr_funcl=None):

	rsm = modin
	if attr_funcl is None:
		for redm in redml:
			rsm = reduce_model_core(rsm, redm)
	else:
		for redm, attr_func in zip(redml, attr_funcl):
			rsm = reduce_model_core(rsm, redm, attr_func)

	return rsm

def align_modules_by_type(srcml, typ, tgtm):

	srcmi = iter(srcml)
	for _name, _module in tgtm.named_modules():
		if isinstance(_module, typ):
			try:
				_obtm = next(srcmi)
			except Exception:
				_obtm = None
			if _obtm is None:
				break
			else:
				add_module(tgtm, _name, _obtm)

	return tgtm

def report_parameters(modin):

	rs = 0
	for _para in modin.parameters():
		rs += _para.numel()

	return rs

def float2odd(fin):

	_rs = ceil(fin)
	if _rs % 2 == 1:
		_rs -= 1

	return _rs

def wrap_float2odd(func):
	@wraps(func)
	def wrapper(*args, **kwargs):
		return float2odd(func(*args, **kwargs))
	return wrapper

def iternext(iterin):

	try:
		rs = next(iterin)
	except:
		rs = None

	return rs

def optm_step_std(optm, model=None, scaler=None, closure=None, multi_gpu=False, multi_gpu_optimizer=False, zero_grad_none=optm_step_zero_grad_set_none):

	if multi_gpu:
		model.collect_gradients()
	if scaler is None:
		optm.step(closure=closure)
	else:
		scaler.step(optm, closure=closure)
		scaler.update()
	if not multi_gpu_optimizer:
		optm.zero_grad(set_to_none=zero_grad_none)
	if multi_gpu:
		model.update_replicas()

def optm_step_wofp16(optm, model=None, scaler=None, closure=None, multi_gpu=False, multi_gpu_optimizer=False, zero_grad_none=optm_step_zero_grad_set_none):

	if multi_gpu:
		model.collect_gradients()
	optm.step(closure=closure)
	if not multi_gpu_optimizer:
		optm.zero_grad(set_to_none=zero_grad_none)
	if multi_gpu:
		model.update_replicas()

optm_step = optm_step_std if fp16_supported else optm_step_wofp16

def divide_para_ind(para_list, ngroup, return_np=False):

	elel = [pu.numel() for pu in para_list]
	n_elel = len(elel)
	if n_elel <= ngroup:
		rs = [(lind, lind + 1,) for lind in range(n_elel)]
	else:
		sum_p = sum(elel)
		p_group = ceil(sum_p / ngroup)
		dp_group = p_group + p_group
		rs = []
		lind = rind = p_g = nd = 0
		nprevs = ngroup - 1
		for elu in elel:
			_np_g = p_g + elu
			if _np_g < p_group:
				rind += 1
				p_g = _np_g
			else:
				if (_np_g + p_g) > dp_group:
					rs.append((lind, rind,))
					lind = rind
					rind += 1
					p_g = elu
				else:
					rind += 1
					rs.append((lind, rind,))
					lind = rind
					p_g = 0
				nd += 1
				if nd >= nprevs:
					break
		rs.append((lind, n_elel,))

	if return_np:
		return rs, [sum(elel[lind:rind]) for lind, rind in rs]
	else:
		return rs

def reorder_by_sort(lins, *inputs, reverse=False):

	td = {}
	for lus, lud in zip(lins, zip(*inputs)):
		if lus in td:
			td[lus].append(lud)
		else:
			td[lus] = [lud]

	rs = None
	for slus in sorted(list(td.keys()), reverse=reverse):
		if rs is None:
			rs = td[slus]
		else:
			rs.extend(td[slus])

	rs = tuple(zip(*rs))

	return rs if len(inputs) > 1 else rs[0]

def range_parameter_iter(model, lind, rind, func=None):

	mp_iter = model.parameters() if func is None else func(model.parameters())
	for i, p in enumerate(mp_iter):
		if i >= lind:
			if i < rind:
				yield p
			else:
				break

def range_parameter_iter_func(model, lind, rind, func=None):

	def iter_func(*args, **kwargs):

		mp_iter = model.parameters() if func is None else func(model.parameters())
		for i, p in enumerate(mp_iter):
			if i >= lind:
				if i < rind:
					yield p
				else:
					break

	return iter_func

def mkdir(pth):

	if not fs_check(pth):
		try:
			makedirs(pth)
		except Exception as e:
			print(e)

class holder(dict):

	def __enter__(self):

		return self

	def get_hold(self, k, sv=None):

		if k in self:
			return self[k]
		else:
			self[k] = sv
			return sv

	def __exit__(self, *inputs, **kwargs):

		pass

class bestfkeeper:

	def __init__(self, fnames=None, k=n_keep_best):

		self.fnames, self.k = [] if fnames is None else fnames, k
		self.clean()

	def update(self, fname=None):

		self.fnames.append(fname)
		self.clean(last_fname=fname)

	def clean(self, last_fname=None):

		_n_files = len(self.fnames)
		_last_fname = (self.fnames[-1] if self.fnames else None) if last_fname is None else last_fname
		while _n_files > self.k:
			fname = self.fnames.pop(0)
			if (fname is not None) and (fname != _last_fname) and fs_check(fname):
				try:
					remove(fname)
				except Exception as e:
					print(e)
			_n_files -= 1
