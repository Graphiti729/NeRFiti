import os
import glob
import copy
import time
import torch
import datetime
import numpy as np


def total_grad_norm(parameters, norm_type=2):
    total_norm = 0
    for p in parameters:
        if p.grad is None:
            continue
        param_norm = p.grad.data.norm(norm_type)
        total_norm += param_norm.item()**norm_type
    total_norm = total_norm**(1. / norm_type)
    return total_norm


def log_train(train_log):
    # iter_i, loss, psnr, grad_norm
    log = [str(i.item()) if type(i) == torch.Tensor else str(i) for i in train_log]
    with open(f'{os.environ["LOG_DIR"]}/loss.txt', 'a') as f:
        f.write(f'{",".join(log)}\n')
    f.close()


def msg_convert(v):
    msg = v
    if isinstance(v, list):
        msg = f"{type(v)}:{len(v)}"
    if isinstance(v, np.ndarray):
        msg = f"{type(v)}:{v.shape}"
    if isinstance(v, np.ndarray):
        msg = f"{type(v)}:{v.shape}"
    if isinstance(v, torch.Tensor):
        msg = f"{str(type(v))}:{v.size()}"
    return msg


def log_cfg(func):
    def wrap(*args, **kwargs):
        with open(f'{os.environ["LOG_DIR"]}/cfg.txt', 'a') as f:
            f.write('='*100+'\n')
            f.write(f"{func.__name__}:\n")
            f.write(f"\tKwargs\n")
            for k, v in kwargs.items():
                msg = msg_convert(v)
                if isinstance(v, dict):
                    msg = copy.deepcopy(v)
                    for key, value in msg.items():
                        msg[key] = msg_convert(value)
                f.write(f"\t\t{k}:{msg}\n")

            f.write(f"\targs\n")
            for v in args:
                msg = v
                if isinstance(v, list):
                    msg = f"{type}:{len(v)}"
                f.write(f"\t\t{msg}\n")
        f.close()

        res = func(*args, **kwargs)
        return res
    wrap.__name__ = func.__name__
    return wrap


def log_time(func):
    def wrap(*args, **kwargs):
        start = time.time()
        res = func(*args, **kwargs)
        print(f"{func.__name__}: {time.time()-start: .4f}")
        return res
    wrap.__name__ = func.__name__
    return wrap


def log_internal(msg):
    with open(f'{os.environ["LOG_DIR"]}/internal.txt', 'a') as f:
        f.write(f'[{datetime.datetime.now().strftime("%H:%M:%S")}]{msg}\n')
    f.close()
    if os.environ['VERBOSE'] == "0":
        print(msg)
