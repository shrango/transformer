#encoding: utf-8

import torch
from torch import nn
from torch.autograd import Function
from torch.nn import functional as nnFunc

from utils.base import reduce_model_list

from math import sqrt

from cnfg.ihyp import *

# 2 kinds of GELU activation function implementation according to https://github.com/huggingface/pytorch-pretrained-BERT/blob/master/pytorch_pretrained_bert/modeling.py#L53-L58

class GeLU_GPT(nn.Module):

	def __init__(self):

		super(GeLU_GPT, self).__init__()

		self.k = sqrt(2.0 / pi)

	def forward(self, x):

		return 0.5 * x * (1.0 + (self.k * (x + 0.044715 * x.pow(3.0))).tanh())

class GeLU_BERT(nn.Module):

	def __init__(self):

		super(GeLU_BERT, self).__init__()

		self.k = sqrt(2.0)

	def forward(self, x):

		return 0.5 * x * (1.0 + (x / self.k).erf())

try:
	GELU = nn.GELU
except Exception as e:
	GELU = GeLU_BERT

# Swish approximates GeLU when beta=1.702 (https://mp.weixin.qq.com/s/LEPalstOc15CX6fuqMRJ8Q).
# GELU is nonmonotonic function that has a shape similar to Swish with beta = 1.4 (https://arxiv.org/abs/1710.05941).
class CustSwish(nn.Module):

	def __init__(self, beta=1.0, freeze_beta=True, isize=None, dim=-1 if adv_act == "normswish" else None, eps=ieps_default):

		super(CustSwish, self).__init__()

		if freeze_beta:
			self.beta = None if beta == 1.0 else beta
			self.reset_beta = None
		else:
			self.reset_beta = beta
			self.beta = nn.Parameter(torch.as_tensor([beta])) if isize is None else nn.Parameter(torch.as_tensor([beta]).repeat(isize))
		self.dim, self.eps = dim, eps

	def forward(self, x):

		if self.dim is None:
			_norm_x = x
		else:
			_dx = x.detach()
			_norm_x = (x - _dx.mean(dim=self.dim, keepdim=True)) / (_dx.std(dim=self.dim, keepdim=True) + self.eps)

		return (x.sigmoid() * _norm_x) if self.beta is None else (_norm_x * (self.beta * x).sigmoid())

	def fix_init(self):

		with torch.no_grad():
			if self.reset_beta is not None:
				self.beta.fill_(self.reset_beta)

try:
	Swish = nn.SiLU
except Exception as e:
	Swish = CustSwish

class SReLU(nn.Module):

	def __init__(self, inplace=False, k=2.0):

		super(SReLU, self).__init__()

		self.inplace, self.k = inplace, k

	def forward(self, x):

		return nnFunc.relu(x, inplace=self.inplace).pow(self.k)

class CustMish(nn.Module):

	def forward(self, x):

		return x * nnFunc.softplus(x).tanh()

try:
	Mish = nn.Mish
except:
	Mish = CustMish

class LGLU(nn.Module):

	def __init__(self, dim=-1):

		super(LGLU, self).__init__()

		self.dim = dim

	def forward(self, x):

		_h, _t = x.tensor_split(2, self.dim)

		return _h * _t

class GLU_Act(LGLU):

	def __init__(self, act=None, dim=-1):

		super(GLU_Act, self).__init__()

		self.dim = dim
		self.act = nn.Sigmoid() if act is None else act

	def forward(self, x):

		_h, _t = x.tensor_split(2, self.dim)

		return self.act(_h) * _t

class GEGLU(GLU_Act):

	def __init__(self, dim=-1):

		_act = GELU()
		super(GEGLU, self).__init__(act=_act, dim=dim)

act_dict = {"swish": Swish, "normswish": Swish, "sigmoid": nn.Sigmoid, "geglu": GEGLU, "srelu": SReLU, "mish": Mish}

def get_act(strin, value=GELU):

	return act_dict.get(strin.lower(), value)

Custom_Act = act_dict.get(adv_act, GELU)

# SparseMax (https://arxiv.org/pdf/1602.02068) borrowed form OpenNMT-py( https://github.com/OpenNMT/OpenNMT-py/blob/master/onmt/modules/sparse_activations.py)
class SparsemaxFunction(Function):

	@staticmethod
	def forward(ctx, input, dim=0):

		def _threshold_and_support(input, dim=0):

			def _make_ix_like(input, dim=0):

				d = input.size(dim)
				rho = torch.arange(1, d + 1, dtype=input.dtype, device=input.device)
				view = [1] * input.dim()
				view[0] = -1

				return rho.view(view).transpose(0, dim)

			input_srt, _ = input.sort(descending=True, dim=dim)
			input_cumsum = input_srt.cumsum(dim) - 1
			rhos = _make_ix_like(input, dim)
			support = rhos * input_srt > input_cumsum

			support_size = support.sum(dim=dim).unsqueeze(dim)
			tau = input_cumsum.gather(dim, support_size - 1)
			tau /= support_size.to(input.dtype, non_blocking=True)

			return tau, support_size

		ctx.dim = dim
		max_val, _ = input.max(dim=dim, keepdim=True)
		input -= max_val
		tau, supp_size = _threshold_and_support(input, dim=dim)
		output = (input - tau).clamp(min=0)
		ctx.save_for_backward(supp_size, output)

		return output

	@staticmethod
	def backward(ctx, grad_output):

		supp_size, output = ctx.saved_tensors
		dim = ctx.dim
		grad_input = grad_output.clone()
		grad_input[output == 0] = 0

		v_hat = grad_input.sum(dim=dim) / supp_size.to(output.dtype, non_blocking=True).squeeze()
		v_hat = v_hat.unsqueeze(dim)
		grad_input = torch.where(output != 0, grad_input - v_hat, grad_input)

		return grad_input, None

class Sparsemax(nn.Module):

	def __init__(self, dim=-1):

		super(Sparsemax, self).__init__()
		self.dim = dim

	def forward(self, input):

		return SparsemaxFunction.apply(input, self.dim)

def reduce_model(modin):

	rsm = reduce_model_list(modin, [nn.ReLU, nn.Softmax, Sparsemax, Swish, GEGLU, SReLU], [lambda m: (m.inplace,), lambda m: (m.dim,), lambda m: (m.dim,), lambda m: (m.reset_beta, m.beta, m.dim, m.eps) if isinstance(Swish, CustSwish) else lambda m: (m.inplace,), lambda m: (m.dim,), lambda m: (m.inplace, m.k,)])

	return reduce_model_list(rsm, [GELU, GeLU_GPT, GeLU_BERT, Mish, nn.Tanh, nn.Sigmoid])
