#!/bin/bash

set -e -o pipefail -x

export cachedir=cache

export dataid=w19ape

export srcd=w19ape
export srctf=train/all.src.tc
export mttf=train/all.mt.tc
export tgttf=train/all.pe.tc
export srcvf=dev/dev.src.tc
export mtvf=dev/dev.mt.tc
export tgtvf=dev/dev.pe.tc

export vratio=0.2
export rratio=0.4
export maxtokens=256

export bpeops=32000
export minfreq=8
export share_bpe=false

export tgtd=$cachedir/$dataid

mkdir -p $tgtd

# clean the data first by removing different translations with lower frequency of same sentences
python tools/clean/ape/maxkeeper.py $srcd/$srctf $srcd/$mttf $srcd/$tgttf $tgtd/src.clean.tmp $tgtd/mt.clean.tmp $tgtd/tgt.clean.tmp $maxtokens
python tools/clean/token_repeat.py $tgtd/src.clean.tmp $tgtd/mt.clean.tmp $tgtd/tgt.clean.tmp $tgtd/src.clean.rtmp $tgtd/mt.clean.rtmp $tgtd/tgt.clean.rtmp $maxtokens
mv $tgtd/src.clean.rtmp $tgtd/src.clean.tmp
mv $tgtd/mt.clean.rtmp $tgtd/mt.clean.tmp
mv $tgtd/tgt.clean.rtmp $tgtd/tgt.clean.tmp

python tools/vocab.py $tgtd/src.clean.tmp $tgtd/src.full.vcb 1048576 &
python tools/vocab.py $tgtd/tgt.clean.tmp $tgtd/tgt.full.vcb 1048576 &
wait
python tools/clean/ape/vocab.py $tgtd/src.clean.tmp $tgtd/mt.clean.tmp $tgtd/tgt.clean.tmp $tgtd/src.train.tok.clean $tgtd/mt.train.tok.clean $tgtd/tgt.train.tok.clean $tgtd/src.full.vcb $tgtd/tgt.full.vcb $vratio
rm -fr $tgtd/src.full.vcb $tgtd/tgt.full.vcb $tgtd/src.clean.tmp $tgtd/mt.clean.tmp $tgtd/tgt.clean.tmp

if $share_bpe; then
# to learn joint bpe
	export src_cdsf=$tgtd/bpe.cds
	export tgt_cdsf=$tgtd/bpe.cds
	subword-nmt learn-joint-bpe-and-vocab --input $tgtd/src.train.tok.clean $tgtd/tgt.train.tok.clean -s $bpeops -o $src_cdsf --write-vocabulary $tgtd/src.vcb.bpe $tgtd/tgt.vcb.bpe
else
# to learn independent bpe:
	export src_cdsf=$tgtd/src.cds
	export tgt_cdsf=$tgtd/tgt.cds
	subword-nmt learn-bpe -s $bpeops < $tgtd/src.train.tok.clean > $src_cdsf &
	subword-nmt learn-bpe -s $bpeops < $tgtd/tgt.train.tok.clean > $tgt_cdsf &
	wait
	subword-nmt apply-bpe -c $src_cdsf < $tgtd/src.train.tok.clean | subword-nmt get-vocab > $tgtd/src.vcb.bpe &
	subword-nmt apply-bpe -c $tgt_cdsf < $tgtd/tgt.train.tok.clean | subword-nmt get-vocab > $tgtd/tgt.vcb.bpe &
	wait
fi

subword-nmt apply-bpe -c $src_cdsf --vocabulary $tgtd/src.vcb.bpe --vocabulary-threshold $minfreq < $tgtd/src.train.tok.clean > $tgtd/src.train.bpe &
subword-nmt apply-bpe -c $tgt_cdsf --vocabulary $tgtd/tgt.vcb.bpe --vocabulary-threshold $minfreq < $tgtd/mt.train.tok.clean > $tgtd/mt.train.bpe &
subword-nmt apply-bpe -c $tgt_cdsf --vocabulary $tgtd/tgt.vcb.bpe --vocabulary-threshold $minfreq < $tgtd/tgt.train.tok.clean > $tgtd/tgt.train.bpe &

subword-nmt apply-bpe -c $src_cdsf --vocabulary $tgtd/src.vcb.bpe --vocabulary-threshold $minfreq < $srcd/$srcvf > $tgtd/src.dev.bpe &
subword-nmt apply-bpe -c $tgt_cdsf --vocabulary $tgtd/tgt.vcb.bpe --vocabulary-threshold $minfreq < $srcd/$mtvf > $tgtd/mt.dev.bpe &
subword-nmt apply-bpe -c $tgt_cdsf --vocabulary $tgtd/tgt.vcb.bpe --vocabulary-threshold $minfreq < $srcd/$tgtvf > $tgtd/tgt.dev.bpe &
wait

# report devlopment set features for cleaning
python tools/check/charatio.py $tgtd/src.dev.bpe $tgtd/tgt.dev.bpe
python tools/check/biratio.py $tgtd/src.dev.bpe $tgtd/tgt.dev.bpe
