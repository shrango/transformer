#encoding: utf-8

# portal from fairseq: https://github.com/pytorch/fairseq/blob/master/scripts/spm_encode.py

import sys
from contextlib import ExitStack
from argparse import ArgumentParser
from sentencepiece import SentencePieceProcessor

def main():

	parser = ArgumentParser()
	parser.add_argument("--model", required=True, help="sentencepiece model to use for encoding")
	parser.add_argument("--inputs", nargs="+", default=["-"], help="input files to filter/encode")
	parser.add_argument("--outputs", nargs="+", default=["-"], help="path to save encoded outputs")
	parser.add_argument("--output_format", choices=["piece", "id"], default="piece")
	parser.add_argument("--min-len", type=int, metavar="N", help="filter sentence pairs with fewer than N tokens")
	parser.add_argument("--max-len", type=int, metavar="N", help="filter sentence pairs with more than N tokens")
	args = parser.parse_args()

	sp = SentencePieceProcessor()
	sp.Load(args.model)

	if args.output_format == "piece":

		def encode(l):
			return sp.EncodeAsPieces(l)

	elif args.output_format == "id":

		def encode(l):
			return list(map(str, sp.EncodeAsIds(l)))

	if args.min_len is not None or args.max_len is not None:

		def valid(line):
			return (args.min_len is None or len(line) >= args.min_len) and (args.max_len is None or len(line) <= args.max_len)

	else:

		def valid(lines):
			return True

	with ExitStack() as stack:
		inputs = [stack.enter_context(open(input, "r", encoding="utf-8")) if input != "-" else sys.stdin for input in args.inputs]
		outputs = [stack.enter_context(open(output, "w", encoding="utf-8")) if output != "-" else sys.stdout for output in args.outputs]

		def encode_line(line):
			line = line.strip()
			if len(line) > 0:
				line = encode(line)
				if valid(line):
					return line
			return None

		for i, lines in enumerate(zip(*inputs), start=1):
			enc_lines = list(map(encode_line, lines))
			if not any(enc_line is None for enc_line in enc_lines):
				for enc_line, output_h in zip(enc_lines, outputs):
					print(" ".join(enc_line), file=output_h)
			if i % 10000 == 0:
				print("processed {} lines".format(i), file=sys.stderr)

if __name__ == "__main__":
	main()
