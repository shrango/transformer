#encoding: utf-8

import sys
from random import seed as rpyseed

from utils.fmt.base import clean_liststr_lentok, all_true, all_le, maxfreq_filter, shuffle_pair, iter_dict_sort, dict_insert_list, dict_insert_set, FileList

# remove_same: reduce same data in the corpus
# shuf: shuffle the data of same source/target length
# max_remove: if one source has several targets, only keep those with highest frequency

def handle(srcfl, tgtfl, max_len=256, remove_same=False, shuf=True, max_remove=False):

	_max_len = max(1, max_len - 2)

	_insert_func = dict_insert_set if remove_same and (not max_remove) else dict_insert_list
	data = {}
	cache = []

	with FileList(srcfl, "rb") as fl:
		for lines in zip(*fl):
			lines = [line.strip() for line in lines]
			if all_true(lines):
				lines, lens = zip(*[clean_liststr_lentok(line.decode("utf-8").split()) for line in lines])
				if all_le(lens, max_len):
					lgth = sum(lens)
					cache.append((lines, lens,))
				else:
					if cache:
						nsent = len(cache)
						lines, lens = zip(*cache)
						lines = zip(*lines)
						lens = zip(*lens)
						mxlens = [max(mu) for mu in lens]
						slens = [sum(mu) for mu in lens]
						lines = tuple("\n".join(lu) for lu in lines)
						data = _insert_func(data, tuple(line.encode("utf-8") for line in lines), nsent, sum(mxlens), *reversed(mxlens[1:]), sum(slens), *reversed(slens[1:]))
						cache = []
			else:
				if cache:
					nsent = len(cache)
					lines, lens = zip(*cache)
					lines = zip(*lines)
					lens = zip(*lens)
					mxlens = [max(mu) for mu in lens]
					slens = [sum(mu) for mu in lens]
					lines = tuple("\n".join(lu) for lu in lines)
					data = _insert_func(data, tuple(line.encode("utf-8") for line in lines), nsent, sum(mxlens), *reversed(mxlens[1:]), sum(slens), *reversed(slens[1:]))
					cache = []

	ens = "\n\n".encode("utf-8")
	with FileList(tgtfl, "wb") as fl:
		for tmp in iter_dict_sort(data):
			lines = zip(*tmp)
			if len(tmp) > 1:
				if max_remove:
					lines = maxfreq_filter(*lines)
				if shuf:
					lines = shuffle_pair(*lines)
			for du, f in zip(lines, fl):
				f.write(ens.join(du))
				f.write(ens)

if __name__ == "__main__":
	rpyseed(666666)
	_nargs = len(sys.argv)
	if _nargs % 2 == 0:
		_sep_ind = _nargs // 2
		handle(sys.argv[1:_sep_ind], sys.argv[_sep_ind:-1], max_len=int(sys.argv[-1]))
	else:
		_sep_ind = (_nargs + 1) // 2
		handle(sys.argv[1:_sep_ind], sys.argv[_sep_ind:])
