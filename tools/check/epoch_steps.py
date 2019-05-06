#encoding: utf-8

import sys

import torch
import h5py

from tqdm import tqdm
from random import shuffle
from random import seed as rpyseed

def handle(h5f, bsize, shuf=True):

	td = h5py.File(h5f, "r")
	ntest = int(td["ndata"][:][0])
	tl = ["t" + str(i) for i in range(ntest)]

	if shuf:
		shuffle(tl)

	ntoken = 0
	nstep = 0
	for tid in tqdm(tl):
		seq_batch = torch.from_numpy(td[tid][:])
		ot = seq_batch.narrow(1, 1, seq_batch.size(1) - 1)
		ntoken += ot.numel() - ot.eq(0).sum().item()
		if ntoken >= bsize:
			nstep += 1
			ntoken = 0

	td.close()

	return nstep

if __name__ == "__main__":
	rpyseed(666666)
	print(handle(sys.argv[1], int(sys.argv[2])))
