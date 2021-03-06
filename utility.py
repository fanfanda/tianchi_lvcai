import os
import time
import shutil
import random
import numpy as np
import pandas as pd
from PIL import Image
from tqdm import tqdm
from collections import OrderedDict
from sklearn.model_selection import train_test_split

import model_v4

import torch
import torch.nn as nn
import torch.optim as optim
import torchvision.models as models
import torchvision.transforms as transforms
from torch.utils.data import Dataset, DataLoader

# 默认使用PIL读图
def default_loader(path):
    # return Image.open(path)
    return Image.open(path).convert('RGB')

# 训练集图片读取
class TrainDataset(Dataset):
    def __init__(self, label_list, transform=None, target_transform=None, loader=default_loader):
        imgs = []
        for index, row in label_list.iterrows():
            imgs.append((row['img_path'], row['label']))
        self.imgs = imgs
        self.transform = transform
        self.target_transform = target_transform
        self.loader = loader

    def __getitem__(self, index):
        filename, label = self.imgs[index]
        img = self.loader(filename)
        if self.transform is not None:
            img = self.transform(img)
        return img, label

    def __len__(self):
        return len(self.imgs)

# 验证集图片读取
class ValDataset(Dataset):
    def __init__(self, label_list, transform=None, target_transform=None, loader=default_loader):
        imgs = []
        for index, row in label_list.iterrows():
            imgs.append((row['img_path'], row['label']))
        self.imgs = imgs
        self.transform = transform
        self.target_transform = target_transform
        self.loader = loader

    def __getitem__(self, index):
        filename, label = self.imgs[index]
        img = self.loader(filename)
        if self.transform is not None:
            img = self.transform(img)
        return img, label

    def __len__(self):
        return len(self.imgs)

# 测试集图片读取
class TestDataset(Dataset):
    def __init__(self, label_list, transform=None, target_transform=None, loader=default_loader):
        imgs = []
        for index, row in label_list.iterrows():
            imgs.append((row['img_path']))
        self.imgs = imgs
        self.transform = transform
        self.target_transform = target_transform
        self.loader = loader

    def __getitem__(self, index):
        filename = self.imgs[index]
        img = self.loader(filename)
        if self.transform is not None:
            img = self.transform(img)
        return img, filename

    def __len__(self):
        return len(self.imgs)

# 数据增强：在给定角度中随机进行旋转
class FixedRotation(object):
    def __init__(self, angles):
        self.angles = angles

    def __call__(self, img):
        return fixed_rotate(img, self.angles)

def fixed_rotate(img, angles):
    angles = list(angles)
    angles_num = len(angles)
    index = random.randint(0, angles_num - 1)
    return img.rotate(angles[index])

# 训练函数
def train(train_loader, model, criterion, optimizer, epoch):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    acc = AverageMeter()

    # switch to train mode
    model.train()

    end = time.time()
    # 从训练集迭代器中获取训练数据
    for i, (images, target) in enumerate(train_loader):
        # 评估图片读取耗时
        data_time.update(time.time() - end)
        # 将图片和标签转化为tensor
        image_var = torch.tensor(images).cuda(async=True)
        label = torch.tensor(target).cuda(async=True)

        # 将图片输入网络，前传，生成预测值
        y_pred = model(image_var)
        # 计算loss
        loss = criterion(y_pred, label)
        losses.update(loss.item(), images.size(0))

        # 计算top1正确率
        prec, PRED_COUNT = accuracy(y_pred.data, target, topk=(1, 1))
        acc.update(prec, PRED_COUNT)

        # 对梯度进行反向传播，使用随机梯度下降更新网络权重
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # 评估训练耗时
        batch_time.update(time.time() - end)
        end = time.time()

        # 打印耗时与结果
        if i % print_freq == 0:
            print('Epoch: [{0}][{1}/{2}]\t'
                    'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                    'Data {data_time.val:.3f} ({data_time.avg:.3f})\t'
                    'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                    'Accuray {acc.val:.3f} ({acc.avg:.3f})'.format(
                epoch, i, len(train_loader), batch_time=batch_time, data_time=data_time, loss=losses, acc=acc))

# 验证函数
def validate(val_loader, model, criterion):
    batch_time = AverageMeter()
    losses = AverageMeter()
    acc = AverageMeter()

    # switch to evaluate mode
    model.eval()

    end = time.time()
    for i, (images, labels) in enumerate(val_loader):
        image_var = torch.tensor(images).cuda(async=True)
        target = torch.tensor(labels).cuda(async=True)

        # 图片前传。验证和测试时不需要更新网络权重，所以使用torch.no_grad()，表示不计算梯度
        with torch.no_grad():
            y_pred = model(image_var)
            loss = criterion(y_pred, target)

        # measure accuracy and record loss
        prec, PRED_COUNT = accuracy(y_pred.data, labels, topk=(1, 1))
        losses.update(loss.item(), images.size(0))
        acc.update(prec, PRED_COUNT)

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % print_freq == 0:
            print('TrainVal: [{0}/{1}]\t'
                    'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                    'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                    'Accuray {acc.val:.3f} ({acc.avg:.3f})'.format(
                i, len(val_loader), batch_time=batch_time, loss=losses, acc=acc))

    print(' * Accuray {acc.avg:.3f}'.format(acc=acc), '(Previous Best Acc: %.3f)' % best_precision,
            ' * Loss {loss.avg:.3f}'.format(loss=losses), 'Previous Lowest Loss: %.3f)' % lowest_loss)
    return acc.avg, losses.avg

# 测试函数
def test(test_loader, model):
    csv_map = OrderedDict({'filename': [], 'probability': []})
    # switch to evaluate mode
    model.eval()
    for i, (images, filepath) in enumerate(tqdm(test_loader)):
        # bs, ncrops, c, h, w = images.size()
        filepath = [os.path.basename(i) for i in filepath]
        image_var = torch.tensor(images, requires_grad=False)  # for pytorch 0.4

        with torch.no_grad():
            y_pred = model(image_var)
            # 使用softmax函数将图片预测结果转换成类别概率
            smax = nn.Softmax(1)
            smax_out = smax(y_pred)
            
        # 保存图片名称与预测概率
        csv_map['filename'].extend(filepath)
        for output in smax_out:
            prob = ';'.join([str(i) for i in output.data.tolist()])
            csv_map['probability'].append(prob)

    result = pd.DataFrame(csv_map)
    result['probability'] = result['probability'].map(lambda x: [float(i) for i in x.split(';')])

    # 转换成提交样例中的格式
    sub_filename, sub_label = [], []
    for index, row in result.iterrows():
        sub_filename.append(row['filename'])
        pred_label = np.argmax(row['probability'])
        if pred_label == 0:
            sub_label.append('norm')
        else:
            sub_label.append('defect%d' % pred_label)

    # 生成结果文件，保存在result文件夹中，可用于直接提交
    submission = pd.DataFrame({'filename': sub_filename, 'label': sub_label})
    submission.to_csv('./result/%s/submission.csv' % file_name, header=None, index=False)
    return

# 保存最新模型以及最优模型
def save_checkpoint(state, is_best, is_lowest_loss, filename='./model/%s/checkpoint.pth.tar' % file_name):
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, './model/%s/model_best.pth.tar' % file_name)
    if is_lowest_loss:
        shutil.copyfile(filename, './model/%s/lowest_loss.pth.tar' % file_name)

# 用于计算精度和时间的变化
class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

# 学习率衰减：lr = lr / lr_decay
def adjust_learning_rate():
    global lr
    lr = lr / lr_decay
    return optim.Adam(model.parameters(), lr, weight_decay=weight_decay, amsgrad=True)

# 计算top K准确率
def accuracy(y_pred, y_actual, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    final_acc = 0
    maxk = max(topk)
    # for prob_threshold in np.arange(0, 1, 0.01):
    PRED_COUNT = y_actual.size(0)
    PRED_CORRECT_COUNT = 0
    prob, pred = y_pred.topk(maxk, 1, True, True)
    # prob = np.where(prob > prob_threshold, prob, 0)
    for j in range(pred.size(0)):
        if int(y_actual[j]) == int(pred[j]):
            PRED_CORRECT_COUNT += 1
    if PRED_COUNT == 0:
        final_acc = 0
    else:
        final_acc = PRED_CORRECT_COUNT / PRED_COUNT
    return final_acc * 100, PRED_COUNT