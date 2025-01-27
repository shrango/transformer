#encoding: utf-8

import torch
from torch import nn
from modules.base import Linear
from modules.act import Custom_Act, GELU
from modules.dropout import Dropout

from utils.plm.base import copy_plm_parameter
#from cnfg.vocab.plm.bert import pad_id

from cnfg.plm.bert.ihyp import *

class Decoder(nn.Module):

	def __init__(self, isize, nwd, num_layer=None, fhsize=None, dropout=0.0, attn_drop=0.0, emb_w=None, num_head=8, model_name="bert", **kwargs):

		super(Decoder, self).__init__()

		self.drop = Dropout(dropout, inplace=True) if dropout > 0.0 else None

		self.ff = nn.Sequential(Linear(isize, isize), Custom_Act() if use_adv_act_default else GELU(), nn.LayerNorm(isize, eps=ieps_ln_default, elementwise_affine=enable_ln_parameters))
		self.classifier = Linear(isize, nwd)
		self.lsm = nn.LogSoftmax(-1)
		if emb_w is not None:
			self.classifier.weight = emb_w
		self.rel_classifier = Linear(isize, 2)
		self.pooler = nn.Sequential(Linear(isize, isize), nn.Tanh())

	def forward(self, inpute, *args, word_prediction=False, **kwargs):

		out = self.ff(inpute)
		if word_prediction:
			out = self.lsm(self.classifier(out))

		return out

	def load_plm(self, plm_parameters, model_name=None, layer_idx=None):

		_model_name = self.model_name if model_name is None else model_name
		with torch.no_grad():
			copy_plm_parameter(self.ff[0].weight, plm_parameters, "cls.predictions.transform.dense.weight")
			_bias_key = "cls.predictions.transform.dense.bias"
			if self.ff[0].bias is None and (_bias_key in plm_parameters):
				self.ff[0].bias = nn.Parameter(torch.zeros(self.ff[0].weight.size(0)))
			if self.ff[0].bias is not None:
				copy_plm_parameter(self.ff[0].bias, plm_parameters, _bias_key)
			copy_plm_parameter(self.ff[2].weight, plm_parameters, "cls.predictions.transform.LayerNorm.weight")
			copy_plm_parameter(self.ff[2].bias, plm_parameters, "cls.predictions.transform.LayerNorm.bias")
			copy_plm_parameter(self.classifier.weight, plm_parameters, "cls.predictions.decoder.weight")
			copy_plm_parameter(self.classifier.bias, plm_parameters, "cls.predictions.bias")
			copy_plm_parameter(self.rel_classifier.weight, plm_parameters, "cls.seq_relationship.weight")
			copy_plm_parameter(self.rel_classifier.bias, plm_parameters, "cls.seq_relationship.bias")
			copy_plm_parameter(self.pooler[0].weight, plm_parameters, "%s.pooler.dense.weight" % _model_name)
			copy_plm_parameter(self.pooler[0].bias, plm_parameters, "%s.pooler.dense.bias" % _model_name)

	def update_classifier(self, indices):

		_nwd = indices.numel()
		_classifier = Linear(self.classifier.weight.size(-1), _nwd, bias=self.classifier.bias is not None)
		with torch.no_grad():
			_classifier.weight.copy_(self.classifier.weight.index_select(0, indices))
			if self.classifier.bias is not None:
				_classifier.bias.copy_(self.classifier.bias.index_select(0, indices))
		self.classifier = _classifier
