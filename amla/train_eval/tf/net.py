# Copyright 2018 Cisco Systems All Rights Reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This code is derived from TensorFlow: https://github.com/tensorflow/models
# by The TensorFlow Authors, Google

""" Net module:
Functions/variables common to both training and evaluation
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import re
import sys
import tarfile
from six.moves import urllib

import tensorflow as tf
import tensorflow.contrib.slim as slim

from stubs.tf import cell_init
from stubs.tf import cell_classification
from stubs.tf import cell_main
from stubs.tf import cifar10_input
from stubs.tf import imagenet_input


# Basic model parameters.
# Global constants describing the CIFAR-10 data set.
NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN = cifar10_input.NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN
NUM_EXAMPLES_PER_EPOCH_FOR_EVAL = cifar10_input.NUM_EXAMPLES_PER_EPOCH_FOR_EVAL

# Constants describing the training process.
# The decay to use for the moving average.
MOVING_AVERAGE_DECAY = 0.9999
# Epochs after which learning rate decays.
NUM_EPOCHS_PER_DECAY = 350
LEARNING_RATE_DECAY_FACTOR = 0.1    # Learning rate decay factor.
INITIAL_LEARNING_RATE = 0.1             # Initial learning rate.

# If a model is trained with multiple GPUs, prefix all Op names with tower_name
# to differentiate the operations. Note that this prefix is removed from the
# names of the summaries when visualizing a model.
TOWER_NAME = 'tower'

DATA_URL = 'https://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz'

"""Net: Neural Net Construction
"""


class Net:
    def __init__(self, base_dir, task_config):
        self.base_dir = base_dir
        self.task_config = task_config
        self.cells = []
        self.end_points = []
        self.nets = []
        self.use_fp16 = False
        self.get_task_params()

    def get_task_params(self):
        self.batch_size = self.task_config["parameters"]["batch_size"]
        self.dataset = self.task_config["parameters"]["dataset"]
        self.image_size = self.task_config["parameters"]["image_size"]
        self.data_dir = self.base_dir + "/" + \
            self.task_config["parameters"]["data_dir"]
        self.arch = self.task_config["arch"]

    def _activation_summary(self, x):
        """Helper to create summaries for activations.

        Creates a summary that provides a histogram of activations.
        Creates a summary that measures the sparsity of activations.

        Args:
            x: Tensor
        Returns:
            nothing
        """
        # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
        # session. This helps the clarity of presentation on tensorboard.
        tensor_name = re.sub('%s_[0-9]*/' % TOWER_NAME, '', x.op.name)
        tf.summary.histogram(tensor_name + '/activations', x)
        tf.summary.scalar(tensor_name + '/sparsity',
                          tf.nn.zero_fraction(x))

    def _variable_on_cpu(self, name, shape, initializer):
        """Helper to create a Variable stored on CPU memory.

        Args:
            name: name of the variable
            shape: list of ints
            initializer: initializer for Variable

        Returns:
            Variable Tensor
        """
        with tf.device('/cpu:0'):
            dtype = tf.float16 if self.use_fp16 else tf.float32
            var = tf.get_variable(
                name, shape, initializer=initializer, dtype=dtype)
        return var

    def _variable_with_weight_decay(self, name, shape, stddev, wdecay):
        """Helper to create an initialized Variable with weight decay.

        Note that the Variable is initialized with a truncated normal distribution.
        A weight decay is added only if one is specified.

        Args:
            name: name of the variable
            shape: list of ints
            stddev: standard deviation of a truncated Gaussian
            wdecay: add L2Loss weight decay multiplied by this float. If None, weight
                    decay is not added for this Variable.

        Returns:
            Variable Tensor
        """
        dtype = tf.float16 if self.use_fp16 else tf.float32
        var = self._variable_on_cpu(
            name,
            shape,
            tf.truncated_normal_initializer(stddev=stddev, dtype=dtype))
        if wdecay is not None:
            weight_decay = tf.multiply(
                tf.nn.l2_loss(var), wdecay, name='weight_loss')
            tf.add_to_collection('losses', weight_decay)
        return var

    def distorted_inputs(self):
        """Construct distorted input for a given dataset using the Reader ops.

        Returns:
            images: Images. 4D tensor of [batch_size, IMAGE_SIZE, IMAGE_SIZE, 3] size.
            labels: Labels. 1D tensor of [batch_size] size.

        Raises:
            ValueError: If no data_dir
        """
        if not self.data_dir:
            raise ValueError('Please supply a data_dir')
        if self.dataset == 'cifar10':
            data_dir = os.path.join(self.data_dir, 'cifar-10-batches-bin')
            images, labels = cifar10_input.distorted_inputs(
                data_dir=data_dir, batch_size=self.batch_size, image_size=self.image_size)
        elif self.dataset == 'imagenet':
            images, labels = imagenet_input.distorted_inputs()
        if self.use_fp16:
            images = tf.cast(images, tf.float16)
            labels = tf.cast(labels, tf.float16)
        return images, labels

    def inputs(self, eval_data):
        """Construct input for CIFAR evaluation using the Reader ops.

        Args:
            eval_data: bool, indicating if one should use the train or eval data set.

        Returns:
            images: Images. 4D tensor of [batch_size, IMAGE_SIZE, IMAGE_SIZE, 3] size.
            labels: Labels. 1D tensor of [batch_size] size.

        Raises:
            ValueError: If no data_dir
        """
        if not self.data_dir:
            raise ValueError('Please supply a data_dir')
        if self.dataset == 'cifar10':
            data_dir = os.path.join(self.data_dir, 'cifar-10-batches-bin')
            images, labels = cifar10_input.inputs(
                eval_data=eval_data, data_dir=data_dir, batch_size=self.batch_size, image_size=self.image_size)
        elif self.dataset == 'imagenet':
            data_dir = self.data_dir
            if self.dataset_split_name == "test":
                self.dataset_split_name = "validation"
            images, labels = imagenet_input.inputs()
        if self.use_fp16:
            images = tf.cast(images, tf.float16)
            labels = tf.cast(labels, tf.float16)
        return images, labels

    def inference(self, images, arch=None,
                  archname=None,
                  initcell=None,
                  classificationcell=None,
                  log_stats=False,
                  is_training=None,
                  scope='Nacnet'
                 ):

        softmax_linear = self.gen_amlanet(
            images,
            arch,
            archname,
            initcell,
            classificationcell,
            log_stats,
            is_training,
            scope)
        return softmax_linear

    def loss(self, logits, labels):
        """Add L2Loss to all the trainable variables.

        Add summary for "Loss" and "Loss/avg".
        Args:
            logits: Logits from inference().
            labels: Labels from distorted_inputs or inputs(). 1-D tensor
                            of shape [batch_size]

        Returns:
            Loss tensor of type float.
        """
        # Calculate the average cross entropy loss across the batch.
        labels = tf.cast(labels, tf.int64)
        cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
            labels=labels, logits=logits, name='cross_entropy_per_example')
        cross_entropy_mean = tf.reduce_mean(
            cross_entropy, name='cross_entropy')
        tf.add_to_collection('losses', cross_entropy_mean)

        # The total loss is defined as the cross entropy loss plus all of the weight
        # decay terms (L2 loss).
        return tf.add_n(tf.get_collection('losses'), name='total_loss')

    def tower_loss(self, scope, logits, labels):
        """Calculate the total loss on a single tower running the CIFAR model.
            Args:
            scope: unique prefix string identifying the CIFAR tower, e.g. 'tower_0'
            images: Images. 4D tensor of shape [batch_size, height, width, 3].
            labels: Labels. 1D tensor of shape [batch_size].
            Returns:
            Tensor of shape [] containing the total loss for a batch of data
        """
        # Build the portion of the Graph calculating the losses. Note that we will
        # assemble the total_loss using a custom function below.
        _ = self.loss(logits, labels)

        # Assemble all of the losses for the current tower only.
        losses = tf.get_collection('losses', scope)

        # Calculate the total loss for the current tower.
        total_loss = tf.add_n(losses, name='total_loss')

        # Attach a scalar summary to all individual losses and the total loss; do the
        # same for the averaged version of the losses.
        for loss in losses + [total_loss]:
            # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU
            # training
            # # session. This helps the clarity of presentation on tensorboard.
            loss_name = re.sub('%s_[0-9]*/' % 'tower', '', loss.op.name)
            tf.summary.scalar(loss_name, loss)

        return total_loss

    def average_gradients(self, tower_grads):
        """Calculate the average gradient for each shared variable across all
           towers.
           Note that this function provides a synchronization point across all
           towers.
        Args:
            tower_grads: List of lists of (gradient, variable) tuples. The outer
            list is over individual gradients. The inner list is over the
            gradient calculation for each tower.
        Returns:
            List of pairs of (gradient, variable) where the gradient has been 
            average across all towers.
        """
        average_grads = []
        for grad_and_vars in zip(*tower_grads):
            # Note that each grad_and_vars looks like the following:
            #   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
            grads = []
            for grad, _ in grad_and_vars:
                """
                gradient for variance and mean calculation is None and hence shouldn't be added to the list of grads
                """
                # if g is not None:
                if True:
                    # Add 0 dimension to the gradients to represent the tower.
                    expanded_g = tf.expand_dims(grad, 0)
                    # Append on a 'tower' dimension which we will average over
                    # below.
                    grads.append(expanded_g)
            '''
            # This break also to avoid adding empty list because of variance and mean calculation
            if len(grads) == 0:
                break
            '''
            # Average over the 'tower' dimension.
            grad = tf.concat(axis=0, values=grads)
            # try:
            grad = tf.reduce_mean(grad, 0)

        # Keep in mind that the Variables are redundant because they are shared
            # across towers. So .. we will just return the first tower's
            # pointer to # the Variable.
            var = grad_and_vars[0][1]
            grad_and_var = (grad, var)
            average_grads.append(grad_and_var)
        return average_grads

    def _add_loss_summaries(self, total_loss):
        """Add summaries for losses in CIFAR-10 model.

        Generates moving average for all losses and associated summaries for
        visualizing the performance of the network.
        Args:
            total_loss: Total loss from loss().
        Returns:
            loss_averages_op: op for generating moving averages of losses.
        """
        # Compute the moving average of all individual losses and the total
        # loss.
        loss_averages = tf.train.ExponentialMovingAverage(0.9, name='avg')
        losses = tf.get_collection('losses')
        loss_averages_op = loss_averages.apply(losses + [total_loss])

        # Attach a scalar summary to all individual losses and the total loss; do the
        # same for the averaged version of the losses.
        for loss in losses + [total_loss]:
            # Name each loss as '(raw)' and name the moving average version of the loss
            # as the original loss name.
            tf.summary.scalar(loss.op.name + ' (raw)', loss)
            tf.summary.scalar(loss.op.name, loss_averages.average(loss))

        return loss_averages_op

    def train(self, total_loss, global_step):
        """Train CIFAR-10 model.

        Create an optimizer and apply to all trainable variables. Add moving
        average for all trainable variables.

        Args:
            total_loss: Total loss from loss().
            global_step: Integer Variable counting the number of training steps
                processed.
        Returns:
            train_op: op for training.
        """
        # Variables that affect learning rate.
        num_batches_per_epoch = NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN / self.batch_size
        decay_steps = int(num_batches_per_epoch * NUM_EPOCHS_PER_DECAY)

        # Decay the learning rate exponentially based on the number of steps.
        learning_rate = tf.train.exponential_decay(INITIAL_LEARNING_RATE,
                                                   global_step,
                                                   decay_steps,
                                                   LEARNING_RATE_DECAY_FACTOR,
                                                   staircase=True)
        tf.summary.scalar('learning_rate', learning_rate)

        # Generate moving averages of all losses and associated summaries.
        loss_averages_op = self._add_loss_summaries(total_loss)

        # Compute gradients.
        with tf.control_dependencies([loss_averages_op]):
            opt = tf.train.GradientDescentOptimizer(learning_rate)
            #opt = tf.train.RMSPropOptimizer(lr, 0.9, 0.9, 1.0)
            grads = opt.compute_gradients(total_loss)

        # Apply gradients.
        apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

        # Add histograms for trainable variables.
        for var in tf.trainable_variables():
            tf.summary.histogram(var.op.name, var)

        # Add histograms for gradients.
        for grad, var in grads:
            if grad is not None:
                tf.summary.histogram(var.op.name + '/gradients', grad)

        # Track the moving averages of all trainable variables.
        variable_averages = tf.train.ExponentialMovingAverage(
            MOVING_AVERAGE_DECAY, global_step)
        variables_averages_op = variable_averages.apply(
            tf.trainable_variables())

        with tf.control_dependencies([apply_gradient_op, variables_averages_op]):
            train_op = tf.no_op(name='train')

        return train_op

    def maybe_download_and_extract(self):
        """Download and extract the tarball from Alex's website."""
        if self.dataset == 'cifar10':
            dest_directory = self.data_dir
            if not os.path.exists(dest_directory):
                os.makedirs(dest_directory)
            filename = DATA_URL.split('/')[-1]
            filepath = os.path.join(dest_directory, filename)
            if not os.path.exists(filepath):
                def _progress(count, block_size, total_size):
                    sys.stdout.write('\r>> Downloading %s %.1f%%' % (filename,
                         float(count * block_size) / float(total_size) * 100.0))
                    sys.stdout.flush()
                filepath, _ = urllib.request.urlretrieve(
                    DATA_URL, filepath, _progress)
                print()
                statinfo = os.stat(filepath)
                print(
                    'Successfully downloaded',
                    filename,
                    statinfo.st_size,
                    'bytes.')
            extracted_dir_path = os.path.join(
                dest_directory, 'cifar-10-batches-bin')
            if not os.path.exists(extracted_dir_path):
                tarfile.open(filepath, 'r:gz').extractall(dest_directory)
        elif self.dataset == 'imagenet':
            """ It is assumed that if imagenet dataset is specified then it already exists
                and not supposed to be downloaded
            """
            if not os.path.exists(self.data_dir):
                print("Directory {} doesn't exist!".format(self.data_dir))
                exit(-1)
        else:
            print("Unknown dataset {}".format(self.dataset))
            exit(-1)

    def add_init(self, inputs, arch, is_training):
        init = cell_init.Init(0, self)
        net = init.cell(inputs, arch, is_training)
        return net

    def add_net(self, net, log_stats, is_training,
                scope,
                arch):

        net = self.gen_network(net, log_stats, is_training,
                               scope,
                               arch)
        return net

    def add_classification(self, net, arch, is_training, cellidx):
        classification = cell_classification.Classification(self, cellidx)
        logits = classification.cell(net, arch, is_training)
        return logits

    def get_params(self):
        for cell in self.cells:
            cell.get_params()

    def gen_network(self, inputs, log_stats=False, is_training=True,
                    scope='Nacnet',
                    arch=None):

        net = inputs
        cellnumber = 1  # Init block is 0
        self.nets = [inputs]

        channelwidth = int(inputs.shape[3])
        for celltype in arch:
            if 'filters' in celltype:
                if "inputs" in celltype.keys():
                    all_inputs = [net]
                    input_dim = net.shape
                    if celltype["inputs"] == "all":
                        for reduced_inputs in self.nets[:-1]:
                            while reduced_inputs.shape[1] != input_dim[1]:
                                reduced_inputs = slim.max_pool2d(
                                    reduced_inputs, [2, 2], padding='SAME')
                            all_inputs.append(reduced_inputs)
                    else:
                        for input_conn in celltype["inputs"]:
                            reduced_inputs = self.nets[input_conn]
                            while reduced_inputs.shape[1] != input_dim[1]:
                                reduced_inputs = slim.max_pool2d(
                                    reduced_inputs, [2, 2], padding='SAME')
                            all_inputs.append(reduced_inputs)
                    net = tf.concat(axis=3, values=all_inputs)
                    num_channels = int(input_dim[3])
                    net = slim.conv2d(
                        net, num_channels, [
                            1, 1], scope='BottleneckLayer_1x1_Envelope_' + str(cellnumber))
                outputs = int(celltype["outputs"] /
                              len(celltype["filters"].keys()))
                envelope = cell_main.CellEnvelope(
                    cellnumber,
                    channelwidth,
                    net,
                    self,
                    filters=celltype["filters"],
                    log_stats=log_stats,
                    outputs=outputs)
                net, end_points = envelope.cell(
                    net, channelwidth, is_training, filters=celltype["filters"])

            #TODO: Move to stubs
            elif 'widener' in celltype:
                nscope = 'Widener_' + str(cellnumber) + '_MaxPool_2x2'
                net1 = slim.max_pool2d(
                    net, [2, 2], scope=nscope, padding='SAME')
                nscope = 'Widener_' + str(cellnumber) + '_conv_3x3'
                net2 = slim.conv2d(
                    net, channelwidth, [
                        3, 3], stride=2, scope=nscope, padding='SAME')
                net = tf.concat(axis=3, values=[net1, net2])
                channelwidth *= 2
            elif 'widener2' in celltype:
                for input_conn in celltype["inputs"]:
                    reduced_inputs = self.nets[input_conn]
                    while(reduced_inputs.shape[1] != input_dim[1]):
                        reduced_inputs = slim.max_pool2d(reduced_inputs, [2,2], padding='SAME')
                    all_inputs.append(reduced_inputs)
                net = tf.concat(axis=3, values=all_inputs)
                num_channels = int(input_dim[3])
                nscope='Widener_'+str(cellnumber)+'_MaxPool_2x2'
                print("Initial #channels={}, after skip={}".format(num_channels, int(net.shape[3])))
                net = slim.max_pool2d(net, [2,2], scope=nscope, padding='SAME')
                channelwidth *= 2
            elif 'outputs' in celltype:
                pass
            else:
                print("Error: Invalid cell definition" + str(celltype))
                exit(-1)

            self.nets.append(net)
            cellnumber += 1
        return net, end_points

    def gen_amlanet(
            self,
            inputs,
            arch=None,
            archname=None,
            initcell=None,
            classificationcell=None,
            log_stats=False,
            is_training=True,
            scope='Amlanet'):
        net = self.add_init(inputs, initcell, is_training)
        end_points = {}
        net, end_points = self.add_net(net, log_stats, is_training,
                                       scope,
                                       arch
                                      )
        cellidx = len(end_points)
        linear_softmax = self.add_classification(
            net, classificationcell, is_training, cellidx)

        summaries_dir = './summaries/'
        logs_path = summaries_dir + "/" + archname
        writer = tf.summary.FileWriter(logs_path, graph=tf.get_default_graph())

        # return logits, end_points
        return linear_softmax
