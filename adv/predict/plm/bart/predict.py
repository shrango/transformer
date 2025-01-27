#encoding: utf-8

import sys

import torch
from transformers import BartTokenizerFast as Tokenizer

from utils.tqdm import tqdm
from utils.h5serial import h5File

import cnfg.plm.bart.base as cnfg
from cnfg.plm.bart.ihyp import *
from cnfg.vocab.plm.roberta import sos_id, eos_id, vocab_size

from transformer.PLM.BART.NMT import NMT
from transformer.EnsembleNMT import NMT as Ensemble
from parallel.parallelMT import DataParallelMT

from utils.base import *
from utils.fmt.base4torch import parse_cuda_decode
from utils.fmt.plm.base import fix_parameter_name

def init_fixing(module):

	if hasattr(module, "fix_init"):
		module.fix_init()

def load_fixing(module):

	if hasattr(module, "fix_load"):
		module.fix_load()

td = h5File(cnfg.test_data, "r")

ntest = td["ndata"][()].item()
detoken = Tokenizer(tokenizer_file=sys.argv[2]).decode

_num_args = len(sys.argv)
if _num_args < 4:
	mymodel = NMT(cnfg.isize, vocab_size, vocab_size, cnfg.nlayer, fhsize=cnfg.ff_hsize, dropout=cnfg.drop, attn_drop=cnfg.attn_drop, global_emb=cnfg.share_emb, num_head=cnfg.nhead, xseql=cache_len_default, ahsize=cnfg.attn_hsize, norm_output=cnfg.norm_output, bindDecoderEmb=cnfg.bindDecoderEmb, forbidden_index=cnfg.forbidden_indexes, model_name=cnfg.model_name)
	mymodel.apply(init_fixing)
	pre_trained_m = cnfg.pre_trained_m
	if pre_trained_m is not None:
		print("Load pre-trained model from: " + pre_trained_m)
		mymodel.load_plm(fix_parameter_name(torch.load(pre_trained_m, map_location="cpu")))
elif _num_args == 4:
	mymodel = NMT(cnfg.isize, vocab_size, vocab_size, cnfg.nlayer, fhsize=cnfg.ff_hsize, dropout=cnfg.drop, attn_drop=cnfg.attn_drop, global_emb=cnfg.share_emb, num_head=cnfg.nhead, xseql=cache_len_default, ahsize=cnfg.attn_hsize, norm_output=cnfg.norm_output, bindDecoderEmb=cnfg.bindDecoderEmb, forbidden_index=cnfg.forbidden_indexes, model_name=cnfg.model_name)
	mymodel = load_model_cpu(sys.argv[3], mymodel)
	mymodel.apply(load_fixing)
else:
	models = []
	for modelf in sys.argv[3:]:
		tmp = NMT(cnfg.isize, vocab_size, vocab_size, cnfg.nlayer, fhsize=cnfg.ff_hsize, dropout=cnfg.drop, attn_drop=cnfg.attn_drop, global_emb=cnfg.share_emb, num_head=cnfg.nhead, xseql=cache_len_default, ahsize=cnfg.attn_hsize, norm_output=cnfg.norm_output, bindDecoderEmb=cnfg.bindDecoderEmb, forbidden_index=cnfg.forbidden_indexes, model_name=cnfg.model_name)
		tmp = load_model_cpu(modelf, tmp)
		tmp.apply(load_fixing)
		models.append(tmp)
	mymodel = Ensemble(models)

mymodel.eval()

use_cuda, cuda_device, cuda_devices, multi_gpu = parse_cuda_decode(cnfg.use_cuda, cnfg.gpuid, cnfg.multi_gpu_decoding)
use_amp = cnfg.use_amp and use_cuda

# Important to make cudnn methods deterministic
set_random_seed(cnfg.seed, use_cuda)

if cuda_device:
	mymodel.to(cuda_device, non_blocking=True)
	if multi_gpu:
		mymodel = DataParallelMT(mymodel, device_ids=cuda_devices, output_device=cuda_device.index, host_replicate=True, gather_output=False)

beam_size = cnfg.beam_size
length_penalty = cnfg.length_penalty

ens = "\n".encode("utf-8")

src_grp = td["src"]
with open(sys.argv[1], "wb") as f, torch.no_grad():
	for i in tqdm(range(ntest), mininterval=tqdm_mininterval):
		seq_batch = torch.from_numpy(src_grp[str(i)][()])
		if cuda_device:
			seq_batch = seq_batch.to(cuda_device, non_blocking=True)
		seq_batch = seq_batch.long()
		with autocast(enabled=use_amp):
			output = mymodel.decode(seq_batch, beam_size, None, length_penalty)
		if multi_gpu:
			tmp = []
			for ou in output:
				tmp.extend(ou.tolist())
			output = tmp
		else:
			output = output.tolist()
		for tran in output:
			# keep sos_id and eos_id is important for the tokenizer implemented by transformers
			tmp = [sos_id]
			for tmpu in tran:
				tmp.append(tmpu)
				if tmpu == eos_id:
					break
			f.write(detoken(tmp, skip_special_tokens=True, clean_up_tokenization_spaces=False).encode("utf-8"))
			f.write(ens)

td.close()
