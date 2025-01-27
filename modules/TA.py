#encoding: utf-8

from modules.base import ResSelfAttn as ResSelfAttnBase, ResCrossAttn as ResCrossAttnBase, PositionwiseFF as PositionwiseFFBase

from cnfg.ihyp import *

class ResSelfAttn(ResSelfAttnBase):

	def forward(self, iQ, *inputs, **kwargs):

		outs = self.net(iQ, *inputs, **kwargs)

		if isinstance(outs, tuple):
			_out = outs[0]

			if self.drop is not None:
				_out = self.drop(_out)

			return self.normer(_out + iQ), *outs[1:]

		else:
			if self.drop is not None:
				outs = self.drop(outs)

			return self.normer(outs + iQ)

class ResCrossAttn(ResCrossAttnBase):

	def forward(self, iQ, iK, *inputs, **kwargs):

		outs = self.net(iQ, iK, *inputs, **kwargs)

		if isinstance(outs, tuple):
			_out = outs[0]

			if self.drop is not None:
				_out = self.drop(_out)

			return self.normer(_out + iQ), *outs[1:]

		else:
			if self.drop is not None:
				outs = self.drop(outs)

			return self.normer(outs + iQ)

class PositionwiseFF(PositionwiseFFBase):

	# isize: input dimension
	# hsize: hidden dimension

	def __init__(self, isize, hsize=None, dropout=0.0, norm_residual=True, **kwargs):

		super(PositionwiseFF, self).__init__(isize, hsize=hsize, dropout=dropout, norm_residual=True, **kwargs)

	def forward(self, x):

		return self.normer(self.net(x) + x)
