import sys, os, time, argparse
import numpy as np
import chainer
import chainer.functions as F
from chainer import Variable, optimizers, cuda, serializers

from source.chainer_functions import loss
from source.data import Data
from source.utils import mkdir_p, load_npz_as_dict
from models.cnn import CNN

import math
XI = 1e-6


def loss_labeled(forward, x, t):
    y = forward(x)
    L = F.softmax_cross_entropy(y, t)
    return L


def loss_unlabeled(forward, x1, args):
    logit = forward(x1)
    wcp_loss = loss.wcp_loss(forward, x1, epsilon=args.epsilon, dr=args.dr, logit=logit)
    ent_y_x = loss.entropy_y_x(logit)
    return wcp_loss + args.gamma * ent_y_x

def loss_test(forward, x, t):
    logit = forward(x)
    L, acc = F.softmax_cross_entropy(logit, t).data, F.accuracy(logit, t).data
    return L, acc


def load_dataset(dirpath, valid=False, dataset_seed=1):
    if valid:
        train_l = load_npz_as_dict(os.path.join(dirpath, 'seed' + str(dataset_seed), 'labeled_train_valid.npz'))
        train_ul = load_npz_as_dict(os.path.join(dirpath, 'seed' + str(dataset_seed), 'unlabeled_train_valid.npz'))
        test = load_npz_as_dict(os.path.join(dirpath, 'seed' + str(dataset_seed), 'test_valid.npz'))
    else:
        train_l = load_npz_as_dict(os.path.join(dirpath, 'seed' + str(dataset_seed), 'labeled_train.npz'))
        train_ul = load_npz_as_dict(os.path.join(dirpath, 'seed' + str(dataset_seed), 'unlabeled_train.npz'))
        test = load_npz_as_dict(os.path.join(dirpath, 'seed' + str(dataset_seed), 'test.npz'))

    train_l['images'] = train_l['images'].reshape(train_l['images'].shape[0], 3, 32, 32).astype(np.float32)
    train_ul['images'] = train_ul['images'].reshape(train_ul['images'].shape[0], 3, 32, 32).astype(np.float32)
    test['images'] = test['images'].reshape(test['images'].shape[0], 3, 32, 32).astype(np.float32)
    return Data(train_l['images'], train_l['labels'].astype(np.int32)), \
           Data(train_ul['images'], train_ul['labels'].astype(np.int32)), \
           Data(test['images'], test['labels'].astype(np.int32))


def train(args):
    np.random.seed(args.seed)
    train_l, train_ul, test = load_dataset(args.data_dir, valid=args.validation, dataset_seed=args.dataset_seed)
    print("N_train_labeled:{}, N_train_unlabeled:{}".format(train_l.N, train_ul.N))
    enc = CNN(n_outputs=args.n_categories, dropout_rate=args.dropout_rate, top_bn=args.top_bn)
    if args.gpu > -1:
        chainer.cuda.get_device(args.gpu).use()
        enc.to_gpu()        
    if args.net != '':
        serializers.load_npz(args.net, enc)

    optimizer = optimizers.Adam(alpha=args.lr, beta1=args.mom1)
    optimizer.setup(enc)
    if args.opt != '':
        serializers.load_npz(args.opt, optimizer)
    
    alpha_plan = [args.lr] * args.num_epochs
    beta1_plan = [args.mom1] * args.num_epochs
    for i in range(args.epoch_decay_start, args.num_epochs):
        alpha_plan[i] = float(args.num_epochs - i) / (args.num_epochs - args.epoch_decay_start) * args.lr
        beta1_plan[i] = args.mom2

    accs_test = np.zeros(args.num_epochs)
    cl_losses = np.zeros(args.num_epochs)
    ul_losses = np.zeros(args.num_epochs)
    mkdir_p(args.log_dir)
    for epoch in range(args.num_epochs):
        with chainer.using_config('train', True):
            optimizer.alpha = alpha_plan[epoch]
            optimizer.beta1 = beta1_plan[epoch]
            sum_loss_l = 0
            sum_loss_ul = 0
            for it in range(args.num_iter_per_epoch):
                start = time.time()
                x1, t = train_l.get(args.batchsize, gpu=args.gpu, aug_trans=args.aug_trans, aug_flip=args.aug_flip)
                loss_l = loss_labeled(enc, Variable(x1), Variable(t))
                x_u1, _ = train_ul.get(args.batchsize_ul, gpu=args.gpu, aug_trans=args.aug_trans, aug_flip=args.aug_flip)
                loss_ul = loss_unlabeled(enc, Variable(x_u1), args)
                beta = args.beta
                loss_total = loss_l + beta * loss_ul
                enc.cleargrads()
                loss_total.backward()
                optimizer.update()
                sum_loss_l += loss_l.data
                sum_loss_ul += loss_ul.data
                end = time.time()
                print("Epoch: {} Iter: {} time_batch: {} beta: {} loss_l: {} loss_ul: {}".format(epoch, it, end-start, beta, loss_l.data, loss_ul.data))
            cl_losses[epoch] = sum_loss_l / args.num_iter_per_epoch
            ul_losses[epoch] = sum_loss_ul / args.num_iter_per_epoch
            
        if (epoch + 1) % args.eval_freq == 0:
            with chainer.using_config('train', False):
                acc_test_sum = 0
                test_x, test_t = test.get()
                N_test = test_x.shape[0]
                for i in range(0, N_test, args.batchsize_eval):
                    x = test_x[i:i + args.batchsize_eval]
                    t = test_t[i:i + args.batchsize_eval]
                    if args.gpu > -1:
                        x, t = cuda.to_gpu(x, device=args.gpu), cuda.to_gpu(t, device=args.gpu)
                    _, acc = loss_test(enc, Variable(x), Variable(t))
                    acc_test_sum += acc * x.shape[0]
                accs_test[epoch] = acc_test_sum / N_test
                print("Epoch:{}, classification loss:{}, unlabeled loss:{}, time:{}".format(
                    epoch, cl_losses[epoch], ul_losses[epoch], end - start))
                print("test acc:{}".format(accs_test[epoch]))
                
        sys.stdout.flush()
        if (epoch + 1) % args.snapshot_freq == 0:
            # Save stats and model
            np.savetxt(os.path.join(args.log_dir, 'log.txt'),
                       np.concatenate([np.array([['acc', 'cl_loss', 'ul_loss']]),
                                       np.transpose([accs_test, cl_losses, ul_losses])], 0), fmt='%s')
            serializers.save_npz(os.path.join(args.log_dir, 'trained_model_ep{}'.format(epoch)), enc)
            serializers.save_npz(os.path.join(args.log_dir, 'optimizer_ep{}'.format(epoch)), optimizer)

    # Save final stats and model
    np.savetxt(os.path.join(args.log_dir, 'log.txt'),
               np.concatenate([np.array([['acc', 'cl_loss', 'ul_loss']]),
                               np.transpose([accs_test, cl_losses, ul_losses])], 0), fmt='%s')
    serializers.save_npz(os.path.join(args.log_dir, 'trained_model_final'), enc)
    serializers.save_npz(os.path.join(args.log_dir, 'optimizer_final'), optimizer)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', type=int, help='which gpu device to use', default=-1)
    parser.add_argument('--data_dir', type=str, default='../dataset/cifar10/')
    parser.add_argument('--log_dir', type=str, default='./log')
    parser.add_argument('--n_categories', type=int, default=10)
    parser.add_argument('--eval_freq', type=int, default=1)
    parser.add_argument('--snapshot_freq', type=int, default=20)
    parser.add_argument('--aug_flip', action='store_true')
    parser.add_argument('--aug_trans', action='store_true')
    parser.add_argument('--validation', action='store_true')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--dataset_seed', type=int, default=1)

    parser.add_argument('--net', default='')
    parser.add_argument('--opt', default='')
    parser.add_argument('--batchsize', type=int, default=32)
    parser.add_argument('--batchsize_ul', type=int, default=128)
    parser.add_argument('--batchsize_eval', type=int, default=100)
    parser.add_argument('--num_epochs', type=int, default=120)
    parser.add_argument('--num_iter_per_epoch', type=int, default=400)
    parser.add_argument('--epoch_decay_start', type=int, default=80)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--mom1', type=float, default=0.9)
    parser.add_argument('--mom2', type=float, default=0.5)

    parser.add_argument('--epsilon', type=float, help='epsilon', default=8.0)
    parser.add_argument('--dropout_rate', type=float, help='dropout_rate', default=0.5)
    parser.add_argument('--top_bn', action='store_true')
    parser.add_argument('--beta', type=float, default=1.)
    parser.add_argument('--dr', type=float, default=0.2)
    parser.add_argument('--gamma', type=float, default=1.)
    args = parser.parse_args()
    print(args)
    train(args)
