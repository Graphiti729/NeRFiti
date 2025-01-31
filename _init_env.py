import os
import torch
import datetime
from functionals import log_internal
import warnings

os.environ['EXP_NAME'] = '-'.join(['TEST', 'SYNC', str(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))])
os.environ['LOG_DIR'] = f'./logs/{os.environ["EXP_NAME"]}'
os.mkdir(os.environ['LOG_DIR'])
os.mkdir(os.path.join(os.environ['LOG_DIR'], 'validations'))
os.environ['VERBOSE'] = "0"
os.environ['DEVICE'] = 'cuda:0'
# os.environ['DEVICE'] = 'cpu'

warnings.filterwarnings(action='ignore')

torch.set_default_tensor_type('torch.cuda.FloatTensor')
torch.backends.cudnn.benchmark = True
torch.autograd.set_detect_anomaly(True)

log_internal("="*100+"\nInteral Log\n")

