#encoding: utf-8

import torch
from torch import nn
from modules.dropout import Dropout
from modules.TA import ResSelfAttn, ResCrossAttn, PositionwiseFF
from utils.sampler import SampleMax
from utils.base import all_done, index_tensors, expand_bsize_for_beam, select_zero_, mask_tensor_type
from utils.plm.base import copy_plm_parameter
from cnfg.vocab.plm.roberta import pad_id, eos_id, pemb_start_ind
from math import sqrt

from transformer.Decoder import DecoderLayer as DecoderLayerBase, Decoder as DecoderBase

from cnfg.plm.bart.base import remove_classifier_bias
from cnfg.plm.bart.ihyp import *

class DecoderLayer(DecoderLayerBase):

	def __init__(self, isize, fhsize=None, dropout=0.0, attn_drop=0.0, num_head=8, ahsize=None, norm_residual=norm_residual_default, k_rel_pos=use_k_relative_position_decoder, max_bucket_distance=relative_position_max_bucket_distance_decoder, model_name="decoder", **kwargs):

		_ahsize = isize if ahsize is None else ahsize
		_fhsize = _ahsize * 4 if fhsize is None else fhsize

		super(DecoderLayer, self).__init__(isize, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, ahsize=_ahsize, norm_residual=norm_residual, k_rel_pos=k_rel_pos, max_bucket_distance=max_bucket_distance, **kwargs)

		self.model_name = model_name

		self.self_attn = ResSelfAttn(isize, _ahsize, num_head=num_head, dropout=attn_drop, norm_residual=norm_residual, enable_bias=enable_prev_ln_bias_default, enable_proj_bias=enable_proj_bias_default, k_rel_pos=k_rel_pos, uni_direction_reduction=True, max_bucket_distance=max_bucket_distance, xseql=cache_len_default)
		self.cross_attn = ResCrossAttn(isize, _ahsize, num_head=num_head, dropout=attn_drop, norm_residual=norm_residual, enable_bias=enable_prev_ln_bias_default, enable_proj_bias=enable_proj_bias_default)
		self.ff = PositionwiseFF(isize, hsize=_fhsize, dropout=dropout, norm_residual=norm_residual, custom_act=use_adv_act_default, enable_bias=enable_prev_ln_bias_default, use_glu=use_glu_ffn)

	def load_plm(self, plm_parameters, model_name=None, layer_idx=None):

		_model_name = self.model_name if model_name is None else model_name
		with torch.no_grad():
			copy_plm_parameter(self.self_attn.net.adaptor.weight, plm_parameters, ["%s.layers.%d.self_attn.q_proj.weight" % (_model_name, layer_idx,), "%s.layers.%d.self_attn.k_proj.weight" % (_model_name, layer_idx,), "%s.layers.%d.self_attn.v_proj.weight" % (_model_name, layer_idx,)], func=torch.cat, func_kwargs={"dim": 0})
			_bias_key = "%s.layers.%d.self_attn.q_proj.bias" % (_model_name, layer_idx,)
			if self.self_attn.net.adaptor.bias is None and (_bias_key in plm_parameters):
				self.self_attn.net.adaptor.bias = nn.Parameter(torch.zeros(self.self_attn.net.adaptor.weight.size(0)))
			if self.self_attn.net.adaptor.bias is not None:
				copy_plm_parameter(self.self_attn.net.adaptor.bias, plm_parameters, [_bias_key, "%s.layers.%d.self_attn.k_proj.bias" % (_model_name, layer_idx,), "%s.layers.%d.self_attn.v_proj.bias" % (_model_name, layer_idx,)], func=torch.cat, func_kwargs={"dim": 0})
			copy_plm_parameter(self.self_attn.net.outer.weight, plm_parameters, "%s.layers.%d.self_attn.out_proj.weight" % (_model_name, layer_idx,))
			_bias_key = "%s.layers.%d.self_attn.out_proj.bias" % (_model_name, layer_idx,)
			if self.self_attn.net.outer.bias is None and (_bias_key in plm_parameters):
				self.self_attn.net.outer.bias = nn.Parameter(torch.zeros(self.self_attn.net.outer.weight.size(0)))
			if self.self_attn.net.outer.bias is not None:
				copy_plm_parameter(self.self_attn.net.outer.bias, plm_parameters, _bias_key)
			copy_plm_parameter(self.self_attn.normer.weight, plm_parameters, "%s.layers.%d.self_attn_layer_norm.weight" % (_model_name, layer_idx,))
			copy_plm_parameter(self.self_attn.normer.bias, plm_parameters, "%s.layers.%d.self_attn_layer_norm.bias" % (_model_name, layer_idx,))
			copy_plm_parameter(self.cross_attn.net.query_adaptor.weight, plm_parameters, "%s.layers.%d.encoder_attn.q_proj.weight" % (_model_name, layer_idx,))
			_bias_key = "%s.layers.%d.encoder_attn.q_proj.bias" % (_model_name, layer_idx,)
			if self.cross_attn.net.query_adaptor.bias is None and (_bias_key in plm_parameters):
				self.cross_attn.net.query_adaptor.bias = nn.Parameter(torch.zeros(self.cross_attn.net.query_adaptor.weight.size(0)))
			if self.cross_attn.net.query_adaptor.bias is not None:
				copy_plm_parameter(self.cross_attn.net.query_adaptor.bias, plm_parameters, _bias_key)
			copy_plm_parameter(self.cross_attn.net.kv_adaptor.weight, plm_parameters, ["%s.layers.%d.encoder_attn.k_proj.weight" % (_model_name, layer_idx,), "%s.layers.%d.encoder_attn.v_proj.weight" % (_model_name, layer_idx,)], func=torch.cat, func_kwargs={"dim": 0})
			_bias_key = "%s.layers.%d.encoder_attn.k_proj.bias" % (_model_name, layer_idx,)
			if self.cross_attn.net.kv_adaptor.bias is None and (_bias_key in plm_parameters):
				self.cross_attn.net.kv_adaptor.bias = nn.Parameter(torch.zeros(self.cross_attn.net.kv_adaptor.weight.size(0)))
			if self.cross_attn.net.kv_adaptor.bias is not None:
				copy_plm_parameter(self.cross_attn.net.kv_adaptor.bias, plm_parameters, [_bias_key, "%s.layers.%d.encoder_attn.v_proj.bias" % (_model_name, layer_idx,)], func=torch.cat, func_kwargs={"dim": 0})
			copy_plm_parameter(self.cross_attn.net.outer.weight, plm_parameters, "%s.layers.%d.encoder_attn.out_proj.weight" % (_model_name, layer_idx,))
			_bias_key = "%s.layers.%d.encoder_attn.out_proj.bias" % (_model_name, layer_idx,)
			if self.cross_attn.net.outer.bias is None and (_bias_key in plm_parameters):
				self.cross_attn.net.outer.bias = nn.Parameter(torch.zeros(self.cross_attn.net.outer.weight.size(0)))
			if self.cross_attn.net.outer.bias is not None:
				copy_plm_parameter(self.cross_attn.net.outer.bias, plm_parameters, _bias_key)
			copy_plm_parameter(self.cross_attn.normer.weight, plm_parameters, "%s.layers.%d.encoder_attn_layer_norm.weight" % (_model_name, layer_idx,))
			copy_plm_parameter(self.cross_attn.normer.bias, plm_parameters, "%s.layers.%d.encoder_attn_layer_norm.bias" % (_model_name, layer_idx,))
			copy_plm_parameter(self.ff.net[0].weight, plm_parameters, "%s.layers.%d.fc1.weight" % (_model_name, layer_idx,))
			copy_plm_parameter(self.ff.net[0].bias, plm_parameters, "%s.layers.%d.fc1.bias" % (_model_name, layer_idx,))
			_l = self.ff.net[-2] if isinstance(self.ff.net[-1], Dropout) else self.ff.net[-1]
			copy_plm_parameter(_l.weight, plm_parameters, "%s.layers.%d.fc2.weight" % (_model_name, layer_idx,))
			_bias_key = "%s.layers.%d.fc2.bias" % (_model_name, layer_idx,)
			if _l.bias is None and (_bias_key in plm_parameters):
				_l.bias = nn.Parameter(torch.zeros(_l.weight.size(0)))
			if _l.bias is not None:
				copy_plm_parameter(_l.bias, plm_parameters, _bias_key)
			copy_plm_parameter(self.ff.normer.weight, plm_parameters, "%s.layers.%d.final_layer_norm.weight" % (_model_name, layer_idx,))
			copy_plm_parameter(self.ff.normer.bias, plm_parameters, "%s.layers.%d.final_layer_norm.bias" % (_model_name, layer_idx,))

class Decoder(DecoderBase):

	def __init__(self, isize, nwd, num_layer, fhsize=None, dropout=0.0, attn_drop=0.0, emb_w=None, num_head=8, xseql=cache_len_default, ahsize=None, norm_output=True, bindemb=True, forbidden_index=None, share_layer=False, disable_pemb=disable_std_pemb_decoder, model_name="decoder", **kwargs):

		_ahsize = isize if ahsize is None else ahsize
		_fhsize = _ahsize * 4 if fhsize is None else fhsize

		super(Decoder, self).__init__(isize, nwd, num_layer, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, emb_w=emb_w, num_head=num_head, xseql=xseql, ahsize=_ahsize, norm_output=norm_output, bindemb=bindemb, forbidden_index=forbidden_index, share_layer=share_layer, disable_pemb=disable_pemb, **kwargs)

		self.model_name = model_name
		self.wemb.padding_idx = pad_id
		self.pemb = None if disable_pemb else nn.Parameter(torch.Tensor(xseql, isize).uniform_(- sqrt(2.0 / (isize + xseql)), sqrt(2.0 / (isize + xseql))))

		if share_layer:
			_shared_layer = DecoderLayer(isize, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, ahsize=_ahsize, model_name=model_name)
			self.nets = nn.ModuleList([_shared_layer for i in range(num_layer)])
		else:
			self.nets = nn.ModuleList([DecoderLayer(isize, fhsize=_fhsize, dropout=dropout, attn_drop=attn_drop, num_head=num_head, ahsize=_ahsize, model_name=model_name) for i in range(num_layer)])

	def forward(self, inpute, inputo, src_pad_mask=None, word_prediction=False):

		nquery = inputo.size(-1)

		out = self.wemb(inputo)
		if self.pemb is not None:
			out = out + self.pemb.narrow(0, pemb_start_ind, nquery)
		if self.out_normer is not None:
			out = self.out_normer(out)
		if self.drop is not None:
			out = self.drop(out)

		_mask = self._get_subsequent_mask(nquery)

		for net in self.nets:
			out = net(inpute, out, src_pad_mask, _mask)

		if word_prediction:
			out = self.lsm(self.classifier(out))

		return out

	def greedy_decode(self, inpute, src_pad_mask=None, max_len=512, fill_pad=False, sample=False):

		bsize = inpute.size(0)

		out = self.get_sos_emb(inpute)
		if self.pemb is not None:
			out = out + self.pemb[pemb_start_ind]
		if self.out_normer is not None:
			out = self.out_normer(out)
		if self.drop is not None:
			out = self.drop(out)

		states = {}

		for _tmp, net in enumerate(self.nets):
			out, _state = net(inpute, (None, None,), src_pad_mask, None, out)
			states[_tmp] = _state

		out = self.classifier(out)
		wds = SampleMax(out.softmax(-1), dim=-1, keepdim=False) if sample else out.argmax(dim=-1)

		trans = [wds]
		done_trans = wds.eq(eos_id)

		for i in range(1, max_len):

			out = self.wemb(wds)
			if self.pemb is not None:
				out = out + self.pemb[pemb_start_ind + i]
			if self.out_normer is not None:
				out = self.out_normer(out)
			if self.drop is not None:
				out = self.drop(out)

			for _tmp, net in enumerate(self.nets):
				out, _state = net(inpute, states[_tmp], src_pad_mask, None, out)
				states[_tmp] = _state

			out = self.classifier(out)
			wds = SampleMax(out.softmax(-1), dim=-1, keepdim=False) if sample else out.argmax(dim=-1)

			trans.append(wds.masked_fill(done_trans, pad_id) if fill_pad else wds)

			done_trans = done_trans | wds.eq(eos_id)
			if all_done(done_trans, bsize):
				break

		return torch.cat(trans, 1)

	def beam_decode(self, inpute, src_pad_mask=None, beam_size=8, max_len=512, length_penalty=0.0, return_all=False, clip_beam=clip_beam_with_lp, fill_pad=False):

		bsize, seql = inpute.size()[:2]

		beam_size2 = beam_size * beam_size
		bsizeb2 = bsize * beam_size2
		real_bsize = bsize * beam_size

		out = self.get_sos_emb(inpute)

		if length_penalty > 0.0:
			lpv = out.new_ones(real_bsize, 1)
			lpv_base = 6.0 ** length_penalty

		if self.pemb is not None:
			out = out + self.pemb[pemb_start_ind]
		if self.out_normer is not None:
			out = self.out_normer(out)
		if self.drop is not None:
			out = self.drop(out)

		states = {}

		for _tmp, net in enumerate(self.nets):
			out, _state = net(inpute, (None, None,), src_pad_mask, None, out)
			states[_tmp] = _state

		out = self.lsm(self.classifier(out))

		scores, wds = out.topk(beam_size, dim=-1)
		scores = scores.squeeze(1)
		sum_scores = scores
		wds = wds.view(real_bsize, 1)
		trans = wds
		_inds_add_beam2 = torch.arange(0, bsizeb2, beam_size2, dtype=wds.dtype, device=wds.device).unsqueeze(1).expand(bsize, beam_size)
		_inds_add_beam = torch.arange(0, real_bsize, beam_size, dtype=wds.dtype, device=wds.device).unsqueeze(1).expand(bsize, beam_size)

		done_trans = wds.view(bsize, beam_size).eq(2)

		self.repeat_cross_attn_buffer(beam_size)

		_src_pad_mask = None if src_pad_mask is None else src_pad_mask.repeat(1, beam_size, 1).view(real_bsize, 1, seql)

		states = expand_bsize_for_beam(states, beam_size=beam_size)

		for step in range(1, max_len):

			out = self.wemb(wds)
			if self.pemb is not None:
				out = out + self.pemb[pemb_start_ind + step]
			if self.out_normer is not None:
				out = self.out_normer(out)
			if self.drop is not None:
				out = self.drop(out)

			for _tmp, net in enumerate(self.nets):
				out, _state = net(inpute, states[_tmp], _src_pad_mask, None, out)
				states[_tmp] = _state

			out = self.lsm(self.classifier(out)).view(bsize, beam_size, -1)

			_scores, _wds = out.topk(beam_size, dim=-1)
			_done_trans_unsqueeze = done_trans.unsqueeze(2)
			_scores = (_scores.masked_fill(_done_trans_unsqueeze.expand(bsize, beam_size, beam_size), 0.0) + sum_scores.unsqueeze(2).repeat(1, 1, beam_size).masked_fill_(select_zero_(_done_trans_unsqueeze.repeat(1, 1, beam_size), -1, 0), -inf_default))

			if length_penalty > 0.0:
				lpv.masked_fill_(~done_trans.view(real_bsize, 1), ((step + 6.0) ** length_penalty) / lpv_base)

			if clip_beam and (length_penalty > 0.0):
				scores, _inds = (_scores.view(real_bsize, beam_size) / lpv.expand(real_bsize, beam_size)).view(bsize, beam_size2).topk(beam_size, dim=-1)
				_tinds = (_inds + _inds_add_beam2).view(real_bsize)
				sum_scores = _scores.view(bsizeb2).index_select(0, _tinds).view(bsize, beam_size)
			else:
				scores, _inds = _scores.view(bsize, beam_size2).topk(beam_size, dim=-1)
				_tinds = (_inds + _inds_add_beam2).view(real_bsize)
				sum_scores = scores

			wds = _wds.view(bsizeb2).index_select(0, _tinds).view(real_bsize, 1)

			_inds = (_inds // beam_size + _inds_add_beam).view(real_bsize)

			trans = torch.cat((trans.index_select(0, _inds), wds.masked_fill(done_trans.view(real_bsize, 1), pad_id) if fill_pad else wds), 1)

			done_trans = (done_trans.view(real_bsize).index_select(0, _inds) | wds.eq(2).squeeze(1)).view(bsize, beam_size)

			_done = False
			if length_penalty > 0.0:
				lpv = lpv.index_select(0, _inds)
			elif (not return_all) and all_done(done_trans.select(1, 0), bsize):
				_done = True

			if _done or all_done(done_trans, real_bsize):
				break

			states = index_tensors(states, indices=_inds, dim=0)

		if (not clip_beam) and (length_penalty > 0.0):
			scores = scores / lpv.view(bsize, beam_size)
			scores, _inds = scores.topk(beam_size, dim=-1)
			_inds = (_inds + _inds_add_beam).view(real_bsize)
			trans = trans.view(real_bsize, -1).index_select(0, _inds)

		if return_all:

			return trans.view(bsize, beam_size, -1), scores
		else:

			return trans.view(bsize, beam_size, -1).select(1, 0)

	# BART starts decoding with the <eos> token
	def get_sos_emb(self, inpute, bsize=None):

		bsize = inpute.size(0) if bsize is None else bsize

		return self.wemb.weight[eos_id].view(1, 1, -1).expand(bsize, 1, -1)

	def fix_init(self):

		self.fix_load()
		with torch.no_grad():
			#self.wemb.weight[pad_id].zero_()
			self.classifier.weight[pad_id].zero_()

	def load_plm(self, plm_parameters, model_name=None, layer_idx=None):

		_model_name = self.model_name if model_name is None else model_name
		with torch.no_grad():
			copy_plm_parameter(self.wemb.weight, plm_parameters, "%s.embed_tokens.weight" % _model_name)
			copy_plm_parameter(self.pemb, plm_parameters, "%s.embed_positions.weight" % _model_name)
			copy_plm_parameter(self.out_normer.weight, plm_parameters, "%s.layernorm_embedding.weight" % _model_name)
			copy_plm_parameter(self.out_normer.bias, plm_parameters, "%s.layernorm_embedding.bias" % _model_name)
			for i, net in enumerate(self.nets):
				net.load_plm(plm_parameters, model_name=_model_name, layer_idx=i)
		# BART does NOT have the bias vector in the classifier
		if remove_classifier_bias:
			self.classifier.bias = None
