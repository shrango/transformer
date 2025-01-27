#encoding: utf-8

from utils.fmt.base import list_reader, line_reader, get_bsize, map_batch, pad_batch
from math import ceil

def batch_loader(finput, fref, ftarget, bsize, maxpad, maxpart, maxtoken, minbsize):

	_f_maxpart = float(maxpart)
	rsi = []
	rsr = []
	rst = []
	nd = maxlen = mlen_i = mlen_r = 0
	for i_d, rd, td in zip(list_reader(finput, keep_empty_line=True), list_reader(fref, keep_empty_line=True), line_reader(ftarget, keep_empty_line=True)):
		lid = len(i_d)
		lrd = len(rd)
		lgth = lid + lrd
		if maxlen == 0:
			maxlen = lgth + min(maxpad, ceil(lgth / _f_maxpart))
			_bsize = get_bsize(maxlen, maxtoken, bsize)
		if (nd < minbsize) or (lgth <= maxlen and nd < _bsize):
			rsi.append(i_d)
			rsr.append(rd)
			rst.append(float(td))
			if lid > mlen_i:
				mlen_i = lid
			if lrd > mlen_r:
				mlen_r = lrd
			nd += 1
		else:
			yield rsi, rsr, rst, mlen_i, mlen_r
			rsi = [i_d]
			rsr = [rd]
			rst = [float(td)]
			mlen_i = lid
			mlen_r = lrd
			maxlen = lgth + min(maxpad, ceil(lgth / _f_maxpart))
			_bsize = get_bsize(maxlen, maxtoken, bsize)
			nd = 1
	if rsi:
		yield rsi, rsr, rst, mlen_i, mlen_r

def batch_mapper(finput, fref, ftarget, vocabi, bsize, maxpad, maxpart, maxtoken, minbsize, custom_batch_loader=None):

	_batch_loader = batch_loader if custom_batch_loader is None else custom_batch_loader
	for i_d, rd, td, mlen_i, mlen_t in _batch_loader(finput, fref, ftarget, bsize, maxpad, maxpart, maxtoken, minbsize):
		rsi, extok_i = map_batch(i_d, vocabi)
		rsr, extok_r = map_batch(rd, vocabi)
		yield rsi, rsr, td, mlen_i + extok_i, mlen_t + extok_r

def batch_padder(finput, fref, ftarget, vocabi, bsize, maxpad, maxpart, maxtoken, minbsize, custom_batch_loader=None, custom_batch_mapper=None):

	_batch_mapper = batch_mapper if custom_batch_mapper is None else custom_batch_mapper
	for i_d, rd, td, mlen_i, mlen_t in _batch_mapper(finput, fref, ftarget, vocabi, bsize, maxpad, maxpart, maxtoken, minbsize, custom_batch_loader=custom_batch_loader):
		yield pad_batch(i_d, mlen_i), pad_batch(rd, mlen_t), td
