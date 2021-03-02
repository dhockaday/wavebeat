import os
import yaml
import glob
import json
import torch
import numpy as np
import torchsummary
from tqdm import tqdm
from itertools import product
import pytorch_lightning as pl
from argparse import ArgumentParser

from beat.tcn import TCNModel
from beat.dstcn import dsTCNModel
from beat.waveunet import WaveUNetModel
from beat.data import DownbeatDataset
from beat.eval import evaluate

torch.backends.cudnn.benchmark = True

parser = ArgumentParser()

# add PROGRAM level args
parser.add_argument('--logdir', type=str, default='./', help='Path to pre-trained model log directory with checkpoint.')
parser.add_argument('--preload', action="store_true")
parser.add_argument('--num_workers', type=int, default=0)
parser.add_argument('--beatles_audio_dir', type=str, default='./data')
parser.add_argument('--beatles_annot_dir', type=str, default='./data')
parser.add_argument('--ballroom_audio_dir', type=str, default='./data')
parser.add_argument('--ballroom_annot_dir', type=str, default='./data')
parser.add_argument('--hainsworth_audio_dir', type=str, default='./data')
parser.add_argument('--hainsworth_annot_dir', type=str, default='./data')
parser.add_argument('--rwc_popular_audio_dir', type=str, default='./data')
parser.add_argument('--rwc_popular_annot_dir', type=str, default='./data')

args = parser.parse_args()

# first out the model type from the yaml file
configfile = os.path.join(args.logdir, 'hparams.yaml')
print(configfile)
if os.path.isfile(configfile):
    with open(configfile) as fp:
        # The FullLoader parameter handles the conversion from YAML
        # scalar values to Python the dictionary format
        config = yaml.load(fp, Loader=yaml.FullLoader)
else:
    raise RuntimeError(f"No hparams.yaml file found in {args.logdir}.")

# find the checkpoint path
ckpts = glob.glob(os.path.join(args.logdir, "checkpoints", "*.ckpt"))
if len(ckpts) < 1:
    raise RuntimeError(f"No checkpoints found in {args.logdir}.")
else:
    ckpt_path = ckpts[-1]

# let the model add what it wants
if config['model_type'] == 'tcn':
    model = TCNModel.load_from_checkpoint(ckpt_path)
elif config['model_type'] == 'lstm':
    model = LSTMModel.load_from_checkpoint(ckpt_path)
elif config['model_type'] == 'waveunet':
    model = WaveUNetModel.load_from_checkpoint(ckpt_path)
elif config['model_type'] == 'dstcn':
    model = dsTCNModel.load_from_checkpoint(ckpt_path)

# move model to GPU
model.to('cuda:0')

# set model to eval mode
model.eval()

datasets = ["beatles", "ballroom", "hainsworth", "rwc_popular"]
results = {} # storage for our result metrics

# set the seed
pl.seed_everything(42)

# evaluate on each dataset using the test set
for dataset in datasets:
    if dataset == "beatles":
        audio_dir = args.beatles_audio_dir
        annot_dir = args.beatles_annot_dir
    elif dataset == "ballroom":
        audio_dir = args.ballroom_audio_dir
        annot_dir = args.ballroom_annot_dir
    elif dataset == "hainsworth":
        audio_dir = args.hainsworth_audio_dir
        annot_dir = args.hainsworth_annot_dir
    elif dataset == "rwc_popular":
        audio_dir = args.rwc_popular_audio_dir
        annot_dir = args.rwc_popular_annot_dir

    test_dataset = DownbeatDataset(audio_dir,
                                    annot_dir,
                                    dataset=dataset,
                                    audio_sample_rate=config['audio_sample_rate'],
                                    target_factor=config['target_factor'],
                                    subset="test",
                                    augment=False,
                                    half=True if config['precision'] == 16 else False,
                                    preload=args.preload)

    test_dataloader = torch.utils.data.DataLoader(test_dataset, 
                                                    shuffle=False,
                                                    batch_size=1,
                                                    num_workers=args.num_workers,
                                                    pin_memory=True)

    # setup tracking of metrics
    results[dataset] = {
        "F-measure" : {
            "beat" : [],
            "downbeat" : []
        }
    }

    for example in tqdm(test_dataloader, ncols=80):
        audio, target, metadata = example

        # move data to GPU
        audio = audio.to('cuda:0')
        target = target.to('cuda:0')

        with torch.no_grad():
            pred = model(audio)

        # move data back to CPU
        pred = pred.cpu()
        target = target.cpu()

        beat_scores, downbeat_scores = evaluate(pred.view(2,-1).numpy(), 
                                                target.view(2,-1).numpy(), 
                                                model.hparams.target_sample_rate)

        results[dataset]['F-measure']['beat'].append(beat_scores['F-measure'])
        results[dataset]['F-measure']['downbeat'].append(downbeat_scores['F-measure'])

    print()
    print(f"{dataset}: avg. F1 beat: {np.mean(results[dataset]['F-measure']['beat'])}   avg. F1 downbeat: {np.mean(results[dataset]['F-measure']['downbeat'])}")
    print()

results_dir = 'results/test.json'
with open(results_dir, 'w') as json_file:
    json.dump(results, json_file, sort_keys=True, indent=4) 
    print(f"Saved results to {results_dir}")