#encoding: utf-8

import sys

from random import shuffle

from cnfg.vocab.base import *

serial_func, deserial_func = repr, eval

tostr = lambda lin: [str(lu) for lu in lin]
toint = lambda lin: [int(lu) for lu in lin]
tofloat = lambda lin: [float(lu) for lu in lin]

def save_objects(fname, *inputs):

	ens = "\n".encode("utf-8")
	with sys.stdout.buffer if fname == "-" else open(fname, "wb") as f:
		for tmpu in inputs:
			f.write(serial_func(tmpu).encode("utf-8"))
			f.write(ens)

def load_objects(fname):

	rs = []
	with sys.stdin.buffer if fname == "-" else open(fname, "rb") as f:
		for line in f:
			tmp = line.strip()
			if tmp:
				rs.append(deserial_func(tmp.decode("utf-8")))

	return tuple(rs) if len(rs) > 1 else rs[0]

def load_states(fname):

	rs = []
	with sys.stdin.buffer if fname == "-" else open(fname, "rb") as f:
		for line in f:
			tmp = line.strip()
			if tmp:
				for tmpu in tmp.decode("utf-8").split():
					if tmpu:
						rs.append(tmpu)

	return rs

def list_reader(fname, keep_empty_line=True, print_func=print):

	with sys.stdin.buffer if fname == "-" else open(fname, "rb") as frd:
		for line in frd:
			tmp = line.strip()
			if tmp:
				tmp = clean_list(tmp.decode("utf-8").split())
				yield tmp
			else:
				if print_func is not None:
					print_func("Reminder: encounter an empty line, which may not be the case.")
				if keep_empty_line:
					yield []

def line_reader(fname, keep_empty_line=True, print_func=print):

	with sys.stdin.buffer if fname == "-" else open(fname, "rb") as frd:
		for line in frd:
			tmp = line.strip()
			if tmp:
				yield tmp.decode("utf-8")
			else:
				if print_func is not None:
					print_func("Reminder: encounter an empty line, which shall not be the case.")
				if keep_empty_line:
					yield ""

def ldvocab(vfile, minf=False, omit_vsize=False, vanilla=False, init_vocab=init_vocab, init_normal_token_id=init_normal_token_id):

	if vanilla:
		rs, cwd = {}, 0
	else:
		rs, cwd = init_vocab.copy(), init_normal_token_id
	if omit_vsize:
		vsize = omit_vsize
	else:
		vsize = False
	for data in list_reader(vfile, keep_empty_line=False):
		freq = int(data[0])
		if (not minf) or freq > minf:
			if vsize:
				ndata = len(data) - 1
				if vsize >= ndata:
					for wd in data[1:]:
						rs[wd] = cwd
						cwd += 1
				else:
					for wd in data[1:vsize + 1]:
						rs[wd] = cwd
						cwd += 1
						ndata = vsize
					break
				vsize -= ndata
				if vsize <= 0:
					break
			else:
				for wd in data[1:]:
					rs[wd] = cwd
					cwd += 1
		else:
			break
	return rs, cwd

def save_vocab(vcb_dict, fname, omit_vsize=False):

	r_vocab = {}
	for k, v in vcb_dict.items():
		if v not in r_vocab:
			r_vocab[v]=[str(v), k]
		else:
			r_vocab[v].append(k)

	freqs = list(r_vocab.keys())
	freqs.sort(reverse=True)

	ens = "\n".encode("utf-8")
	remain = omit_vsize
	with sys.stdout.buffer if fname == "-" else open(fname, "wb") as f:
		for freq in freqs:
			cdata = r_vocab[freq]
			ndata = len(cdata) - 1
			if remain and (remain < ndata):
				cdata = cdata[:remain + 1]
				ndata = remain
			f.write(" ".join(cdata).encode("utf-8"))
			f.write(ens)
			if remain:
				remain -= ndata
				if remain <= 0:
					break

def reverse_dict(din):

	return {v:k for k, v in din.items()}

def ldvocab_list(vfile, minf=False, omit_vsize=False):

	rs = []
	if omit_vsize:
		vsize = omit_vsize
	else:
		vsize = False
	cwd = 0
	for data in list_reader(vfile, keep_empty_line=False):
		freq = int(data[0])
		if (not minf) or freq > minf:
			if vsize:
				ndata = len(data) - 1
				if vsize >= ndata:
					rs.extend(data[1:])
					cwd += ndata
				else:
					rs.extend(data[1:vsize + 1])
					cwd += vsize
					break
				vsize -= ndata
				if vsize <= 0:
					break
			else:
				rs.extend(data[1:])
				cwd += len(data) - 1
		else:
			break

	return rs, cwd

def clean_str(strin):

	return " ".join([tmpu for tmpu in strin.split() if tmpu])

def clean_list(lin):

	return [tmpu for tmpu in lin if tmpu]

def clean_list_iter(lin):

	for lu in lin:
		if lu:
			yield lu

def clean_liststr_lentok(lin):

	rs = [tmpu for tmpu in lin if tmpu]

	return " ".join(rs), len(rs)

def maxfreq_filter_core(ls, lt):

	tmp = {}
	for us, ut in zip(ls, lt):
		if us in tmp:
			tmp[us][ut] = tmp[us].get(ut, 0) + 1
		else:
			tmp[us] = {ut: 1}

	rls, rlt = [], []
	for tus, tlt in tmp.items():
		_rs = []
		_maxf = 0
		for key, value in tlt.items():
			if value > _maxf:
				_maxf = value
				_rs = [key]
			elif value == _maxf:
				_rs.append(key)
		for tut in _rs:
			rls.append(tus)
			rlt.append(tut)

	return rls, rlt

def maxfreq_filter(*inputs):

	if len(inputs) > 2:
		# here we assume that we only have one target and it is at the last position
		rsh, rst = maxfreq_filter_core(tuple(zip(*inputs[0:-1])), inputs[-1])
		return *zip(*rsh), rst
	else:
		return maxfreq_filter_core(*inputs)

def shuffle_pair(*inputs):

	tmp = list(zip(*inputs))
	shuffle(tmp)

	return zip(*tmp)

def get_bsize(maxlen, maxtoken, maxbsize):

	rs = max(maxtoken // maxlen, 1)
	if (rs % 2 == 1) and (rs > 1):
		rs -= 1

	return min(rs, maxbsize)

def no_unk_mapper(vcb, ltm, print_func=None):

	if print_func is None:
		return [vcb[wd] for wd in ltm if wd in vcb]
	else:
		rs = []
		for wd in ltm:
			if wd in vcb:
				rs.append(vcb[wd])
			else:
				print_func("Error mapping: "+ wd)
		return rs

def list2dict(lin, kfunc=None):

	return {k: lu for k, lu in enumerate(lin)} if kfunc is None else {kfunc(k): lu for k, lu in enumerate(lin)}

def dict_is_list(sdin, kfunc=None):

	_lset = set(range(len(sdin))) if kfunc is None else set(kfunc(i) for i in range(len(sdin)))

	return False if (_lset - sdin) else True

def dict2pairs(dict_in):

	rsk = []
	rsv = []
	for key, value in dict_in.items():
		rsk.append(key)
		rsv.append(value)

	return rsk, rsv

def iter_dict_sort(dict_in, reverse=False, free=False):

	d_keys = list(dict_in.keys())
	d_keys.sort(reverse=reverse)
	for d_key in d_keys:
		d_v = dict_in[d_key]
		if isinstance(d_v, dict):
			yield from iter_dict_sort(d_v, reverse=reverse, free=free)
		else:
			yield d_v
	if free:
		dict_in.clear()

def dict_insert_set(dict_in, value, *keys):

	if len(keys) > 1:
		_cur_key = keys[0]
		dict_in[_cur_key] = dict_insert_set(dict_in.get(_cur_key, {}), value, *keys[1:])
	else:
		key = keys[0]
		if key in dict_in:
			_dset = dict_in[key]
			if value not in _dset:
				_dset.add(value)
		else:
			dict_in[key] = set([value])

	return dict_in

def dict_insert_list(dict_in, value, *keys):

	if len(keys) > 1:
		_cur_key = keys[0]
		dict_in[_cur_key] = dict_insert_list(dict_in.get(_cur_key, {}), value, *keys[1:])
	else:
		key = keys[0]
		if key in dict_in:
			dict_in[key].append(value)
		else:
			dict_in[key] = [value]

	return dict_in

def legal_vocab(sent, ilgset, ratio):

	total = ilg = 0
	for tmpu in sent.split():
		if tmpu:
			if tmpu in ilgset:
				ilg += 1
			total += 1
	rt = float(ilg) / float(total)

	return False if rt > ratio else True

def all_in(lin, setin):

	return all(lu in setin for lu in lin)

def all_le(lin, value):

	return all(lu <= value for lu in lin)

def all_gt(lin, value):

	return all(lu > value for lu in lin)

def get_char_ratio(strin):

	ntokens = nchars = nsp = 0
	pbpe = False
	for tmpu in strin.split():
		if tmpu:
			if tmpu.endswith("@@"):
				nchars += 1
				if not pbpe:
					pbpe = True
					nsp += 1
			elif pbpe:
				pbpe = False
			ntokens += 1
	lorigin = float(len(strin.replace("@@ ", "").split()))
	ntokens = float(ntokens)

	return float(nchars) / ntokens, ntokens / lorigin, float(nsp) / lorigin

def get_bi_ratio(ls, lt):

	if ls > lt:
		return float(ls) / float(lt)
	else:
		return float(lt) / float(ls)

def map_batch_core(i_d, vocabi, use_unk=use_unk, sos_id=sos_id, eos_id=eos_id, unk_id=unk_id, **kwargs):

	if isinstance(i_d[0], (tuple, list,)):
		return [map_batch_core(idu, vocabi, use_unk=use_unk, sos_id=sos_id, eos_id=eos_id, unk_id=unk_id, **kwargs) for idu in i_d]
	else:
		rsi = [sos_id]
		rsi.extend([vocabi.get(wd, unk_id) for wd in i_d] if use_unk else no_unk_mapper(vocabi, i_d))#[vocabi[wd] for wd in i_d if wd in vocabi]
		rsi.append(eos_id)
		return rsi

def map_batch(i_d, vocabi, use_unk=use_unk, sos_id=sos_id, eos_id=eos_id, unk_id=unk_id, **kwargs):

	return map_batch_core(i_d, vocabi, use_unk=use_unk, sos_id=sos_id, eos_id=eos_id, unk_id=unk_id, **kwargs), 2

def pad_batch(i_d, mlen_i, pad_id=pad_id):

	if isinstance(i_d[0], (tuple, list,)):
		return [pad_batch(idu, mlen_i, pad_id=pad_id) for idu in i_d]
	else:
		curlen = len(i_d)
		if curlen < mlen_i:
			i_d.extend([pad_id for i in range(mlen_i - curlen)])
		return i_d

class FileList(list):

	def __init__(self, files, *inputs, **kwargs):

		super(FileList, self).__init__(open(fname, *inputs, **kwargs) for fname in files)

	def __enter__(self):

		return self

	def __exit__(self, *inputs, **kwargs):

		for _f in self:
			_f.close()

def multi_line_reader(fname, *inputs, num_line=1, **kwargs):

	_i = 0
	rs = []
	_enc = ("rb" in inputs) or ("rb" in kwargs.values())
	ens = "\n".encode("utf-8") if _enc else "\n"
	with (sys.stdin.buffer if _enc else sys.stdin) if fname == "-" else open(fname, *inputs, **kwargs) as frd:
		for line in frd:
			tmp = line.rstrip()
			rs.append(tmp)
			_i += 1
			if _i >= num_line:
				yield ens.join(rs)
				rs = []
				_i = 0
	if rs:
		yield ens.join(rs)
