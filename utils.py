from datetime import datetime
import pprint

import numpy as np
import tensorflow as tf
from tensorflow.contrib.layers import xavier_initializer as glorot
from sklearn.utils import shuffle as group_shuffle

class Progress():
  """ Pretty print progress for neural net training """
  def __init__(self, batches, progress_bar=True, bar_length=30):
    self.progress_bar = progress_bar # boolean
    self.bar_length = bar_length
    self.t1 = datetime.now()
    self.train_start_time = self.t1
    self.batches = batches
    self.current_batch = 0
    self.epoch = 0

  def epoch_start(self):
    self.t1 = datetime.now()
    self.epoch += 1
    self.current_batch = 0 # reset batch

  def epoch_end(self):
    print()

  def print_train(self, loss):
    t2 = datetime.now()
    epoch_time = (t2 - self.t1).total_seconds()
    total_time = (t2 - self.train_start_time).total_seconds()/60
    print('{:2.0f}: sec: {:>5.1f} | total min: {:>5.1f} | train loss: {:>3.4f} '.format(
        self.epoch, epoch_time, total_time, loss), end='')
    self.print_bar()

  def print_cust(self, msg):
    """ Print anything, append previous """
    print(msg, end='')

  def print_eval(self, msg, value):
    print('| {}: {:>3.4f} '.format(msg, value), end='')

  def print_bar(self):
    self.current_batch += 1
    end = '' if self.current_batch == self.batches else '\r'
    bars_full = int(self.current_batch/self.batches*self.bar_length)
    bars_empty = self.bar_length - bars_full
    print("| [{}{}] ".format(u"\u2586"*bars_full, '-'*bars_empty),end=end)

def make_batches_legacy(data, batch_size, num_batches,shuffle=True):
  """ Batches the passed data
  Args:
    data       : a list of numpy arrays
    batch_size : int
    shuffle    : should be true except when testing
  Returns:
    list of original numpy arrays but sliced
  """
  sk_seed = np.random.randint(0,10000)
  if shuffle: data = group_shuffle(*data, random_state=sk_seed)
  data_size = len(data[0])
  for batch_num in range(num_batches):
    start_index = batch_num * batch_size
    end_index = min((batch_num + 1) * batch_size, data_size)
    batch = []
    for d in data:
      batch.append(d[start_index:end_index])
    yield batch

class Metrics():
  """ Keeps score of metrics during training """
  def __init__(self, monitor):
    """
    Arg:
      monitor: best results based on this metric
    """
    self.monitor = monitor
    self.metric_best = 0
    self.metric_current = 0
    self._metric_dict = {"test_f1": 0}
    self.epoch_current = 0
    self.epoch_best = 0

  def __str__(self):
    return pprint.pformat(self.metric_dict)

  def update(self, name, value):
    """ Only save metric if best for monitored """
    if name == self.monitor:
      self._check_if_best(name, value)
    else:
      if self.epoch_current == self.epoch_best:
        self._metric_dict[name] = value

  def _check_if_best(self, name, value):
    self.epoch_current += 1
    self.metric_current = value
    if value >= self.metric_best:
      self._metric_dict[name] = value
      self.metric_best = value
      self.epoch_best = self.epoch_current
      self._metric_dict['epoch_best'] = self.epoch_best

  @property
  def metric_dict(self):
    """ Get the dictionary of metrics """
    return self._metric_dict

class Callback():
  """ Monitor training """
  def __init__(self, early_stop_epoch, metrics, prog_bar):
    """
    Args:
      early_stop_epoch : stop if not improved for these epochs
      metrics: a Metrics object with f1 property updated during training
    """
    self.metrics = metrics
    self.early_stop_epoch = early_stop_epoch
    self.stop_count = 0
    self.prog = prog_bar

  def early_stop(self):
    """ Check if f1 is decreasing """
    if self.metrics.metric_current < self.metrics.metric_best:
      self.stop_count += 1
    else:
      self.stop_count = 0

    if self.stop_count >= self.early_stop_epoch:
      self.prog.print_cust("\nEarly stopping")
      return True
    else:
      return False

class TrainEmbeddings():
  """ Retrain embeddings on dataset for x epochs """
  def __init__(self):
    self.embeddings_original
    self.vocab
    self.inv_vocab
    pass

  def train():
    pass

def dense(x, in_dim, out_dim, scope, act=None):
  """ Fully connected layer builder"""
  with tf.variable_scope(scope):
    weights = tf.get_variable("weights", shape=[in_dim, out_dim],
              dtype=tf.float32, initializer=glorot())
    biases = tf.get_variable("biases", out_dim,
              dtype=tf.float32, initializer=tf.constant_initializer(0.0))
    # Pre activation
    h = tf.matmul(x,weights) + biases
    # Post activation
    if act:
      h = act(h)
    return h
