# -*- coding: utf-8 -*-
# @Author: vamshi
# @Date:   2018-02-04 00:39:34
# @Last Modified by:   vamshi
# @Last Modified time: 2018-02-10 12:32:44

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
sys.path.append("../")
import dateutil.parser
import pickle
import os
import time
import pandas as pd
import numpy as np
import mmap
import argparse

from six.moves import cPickle
from functools import wraps
from tqdm import tqdm
from datetime import datetime
#from utils import hour2vec
import tensorflow as tf

from utils import *

lambdim = 180*24/0.5


def sur_loss_impl(lamb,b,e,sessLengths,dt,T):
        '''
                lamb: predicted lambda from rnn [batch_size, maxSessLen,lambdim]
                e : end times of sessions batch_sizep [batch_size,maxSessLen]
                g : gap times of sessions batch_sizep [batch_size,maxSessLen]
                dt: 30mts 
        '''
        loss = np.zeros(shape=(e.shape[0],),dtype=np.float32)
        for i in range(lamb.shape[0]):
                seslen = sessLengths[i]
                st_time = b[i][0]
                #calculate third term
                loss3 = np.sum(lamb[i][seslen-1])*dt
                loss2 = 0
                for j in range(1,seslen):
                        loss2 += np.log(lamb[i][j][int(np.round((b[i][j]-e[i][j-1])/dt))])
                loss1=0
                for j in range(1,seslen):
                        et = e[i][j-1] - st_time
                        bnext = b[i][j] -st_time
                        dif = int((bnext -et)/dt)
                        for k in range(dif):
                                loss1 += lamb[i][j][k]

                l = loss2+loss3+loss1
                loss[i] = l

        return np.float32(np.sum(loss))


def sur_loss(lamb,b,e,sessLengths,dt,T):
    tf.RegisterGradient("sur_loss_grad")(sur_loss_grad)
    g=tf.get_default_graph()
    with g.gradient_override_map({"PyFunc":"sur_loss_grad"}):
        return tf.py_func(sur_loss_impl,[lamb,b,e,sessLengths,dt,T],[tf.float32])[0]

def sur_loss_grad_impl(lamb):
        grad = np.zeros_like(lamb)
        #num_batches

        return [np.float32(grad),np.int32(0),np.int32(0),np.int32(0),np.int32(0),np.int32(0)]

def sur_loss_grad(op,grad):
    lamb,b,e,sessLengths,dt,T=op.inputs[0],op.inputs[1],op.inputs[2],op.inputs[3],op.inputs[4],op.inputs[5]
    return tf.py_func(sur_loss_grad_impl,[lamb],[tf.float32,tf.int32,tf.int32,tf.int32,tf.int32,tf.int32])#assume grad=1



def build_model(args,maxSessLen,sessLengths,gaps,d):

        ''' 
                gaps,d: [batch_size,maxSessLen]
                inputs : tuple (gaps,d,u) gaps dim: batch_size*maxSessionLen
                inputX : dim: [batch_size, maxSessLen, 3*embed_dim]
        '''
        graph =  tf.get_default_graph()
        with tf.variable_scope("gap_embedding"):
                gap_embedding = tf.get_variable("gap_embedding",[args.n_gaps, args.embed_dim])
        with tf.variable_scope("d_embedding"):
                d_embedding   = tf.get_variable("d_embedding",[168,args.embed_dim])
        #user_embedding = tf.get_variable("user_embedding",[args.num_users, args.embed_dim])

        gap_embedded = tf.nn.embedding_lookup(gap_embedding, gaps)
        d_embedded   = tf.nn.embedding_lookup(d_embedding, d)
        #user_embedded = tf.nn.embedding_lookup(user_embedding, u)

        inputX = tf.concat((gap_embedded,d_embedded), axis=2)

        with tf.variable_scope("cell_def"):
                lstm_cell = tf.contrib.rnn.LSTMCell(args.n_hidden)
        with tf.variable_scope("rnn_def"):
                output,states = tf.nn.dynamic_rnn(lstm_cell, inputX, sequence_length=sessLengths, time_major=False,dtype=tf.float32) 

        W = tf.get_variable("weights", (args.batch_size,args.n_hidden,args.lambdim),
        initializer=tf.random_normal_initializer())
        #b = tf.get_variable("biases", bias_shape,initializer=tf.constant_initializer(0.0))
        lamb = tf.matmul(output, W)
        return tf.nn.softplus(lamb)


class Model():
        def __init__(self,args,maxSessLen):
                self.args = args
                self.maxSessLen = maxSessLen

        def build_graph(self,args,maxSessLen):
                self.graph = tf.Graph()
                with self.graph.as_default():

                        self.inputg = tf.placeholder(dtype=tf.int32, shape=(args.batch_size,maxSessLen))
                        self.inputd = tf.placeholder(dtype=tf.int32, shape=(args.batch_size,maxSessLen))
                        self.inputb = tf.placeholder(dtype=tf.int32, shape=(args.batch_size,maxSessLen))
                        self.inpute = tf.placeholder(dtype=tf.int32, shape=(args.batch_size,maxSessLen))

                        self.target_gaps = tf.placeholder(dtype=tf.int32, shape=(args.batch_size,1))

                        self.sessLengths = tf.placeholder(dtype=tf.int32,shape=[args.batch_size])
                        self.lamb = build_model(args,maxSessLen,self.sessLengths,self.inputg,self.inputd)

                        self.loss = sur_loss(self.lamb,self.inputb,self.inpute,self.sessLengths,args.dt,args.T)

                        self.var_op = tf.global_variables()
                        self.var_trainable_op = tf.trainable_variables()
                        if args.grad_clip == -1:
                                self.optimizer = tf.train.AdamOptimizer(args.learning_rate).minimize(self.loss)
                        else:
                                grads, _ = tf.clip_by_global_norm(tf.gradients(self.loss, self.var_trainable_op), args.grad_clip)
                                opti = tf.train.AdamOptimizer(args.learning_rate)
                                self.optimizer = opti.apply_gradients(zip(grads, self.var_trainable_op))
                        self.initial_op = tf.initialize_all_variables()
                        self.summary_op = tf.summary.merge_all()
                        self.saver = tf.train.Saver(tf.global_variables(), max_to_keep=5, keep_checkpoint_every_n_hours=1)



#if __name__ == "__main__":
    #objName = Model()
    #objName.build_graph() 