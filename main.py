"""
-----------
Description
-----------
Implicit DRR with Seq2Seq with Attention
Author: Andre Cianflone

For a single trial, call script without arguments

For hyperparameter search, call as this example:
python main.py --trials 50 --search_param cell_units --file_save trials/cell_units
-----------
"""
from helper import Preprocess, Data, MiniData, make_batches, settings
from embeddings import Embeddings
import tensorflow as tf
from sklearn.metrics import f1_score, accuracy_score
import numpy as np
from enc_dec import EncDec
from utils import Progress, Metrics, Callback
import sys
from six.moves import cPickle as pickle
from hyperopt import fmin, tpe, hp, STATUS_OK, Trials
from pprint import pprint
import codecs
import json
import argparse

###############################################################################
# Data
###############################################################################
max_arg_len = 60              # max length of each arg
maxlen      = max_arg_len * 2 # max num of tokens per sample

# Settings file
settings = settings('settings.json')
dataset_name = settings['use_dataset']

data_class = Preprocess(
            # dataset_name='conll',
            dataset_name = dataset_name,
            relation = settings[dataset_name]['this_relation'],
            max_vocab = settings['max_vocab'],
            random_negative=False,
            max_arg_len=max_arg_len,
            maxlen=maxlen,
            settings=settings,
            split_input=True,
            pad_tag = settings['pad_tag'],
            unknown_tag = settings['unknown_tag'],
            bos_tag = settings['bos_tag'],
            eos_tag = settings['eos_tag'])

# Data sets as Data objects
dataset_dict = data_class.data_collect

# Word embeddings
emb = Embeddings(
    data_class.vocab,
    data_class.inv_vocab,
    random_init_unknown=settings['random_init_unknown'],
    unknown_tag = settings['unknown_tag'])

# embedding is a numpy array [vocab size x embedding dimension]
embedding = emb.get_embedding_matrix(\
            word2vec_model_path=settings['embedding']['model_path'],
            small_model_path=settings['embedding']['small_model_path'],
            save=True,
            load_saved=True)

###############################################################################
# Main stuff
###############################################################################
def call_model(sess, model, data, fetch, batch_size, num_batches, keep_prob,
              shuffle):
  """ Calls models and yields results per batch """
  batches = make_batches(data, batch_size, num_batches, shuffle=shuffle)
  for batch in batches:
    feed = {
             model.enc_input       : batch.encoder_input,
             model.enc_input_len   : batch.seq_len_encoder,
             model.classes         : batch.classes,
             model.dec_targets     : batch.decoder_target,
             model.dec_input       : batch.decoder_input,
             model.dec_input_len   : batch.seq_len_decoder,
             model.dec_weight_mask : batch.decoder_mask,
             model.keep_prob       : keep_prob
           }

    result = sess.run(fetch,feed)
    # yield the results when training
    yield result

def train_one_epoch(sess, data, model, keep_prob, batch_size, num_batches,
                    prog, writer=None):
  """ Train 'model' using 'data' for a single epoch """
  fetch = [model.class_optimizer, model.class_cost]

  if writer is not None:
    fetch.append(model.merged_summary_ops)

  batch_results = call_model(sess, model, data, fetch, batch_size, num_batches,
                             keep_prob, shuffle=True)
  i = 0
  for result in batch_results:
    loss = result[1]
    summary = result[-1]
    prog.print_train(loss)
    if writer is not None:
      i += 1
      writer.add_summary(summary, i)
    # break

def classification_f1(sess, data, model, batch_size, num_batches_test):
  """ Get the total loss for the entire batch """
  fetch = [model.batch_size, model.class_cost, model.y_pred, model.y_true]
  y_pred = np.zeros(data.size())
  y_true = np.zeros(data.size())
  batch_results = call_model(sess, model, data, fetch, batch_size,
                             num_batches_test, keep_prob=1, shuffle=False)
  start_id = 0
  for i, result in enumerate(batch_results):
    batch_size                           = result[0]
    cost                                 = result[1]
    y_pred[start_id:start_id+batch_size] = result[2]
    y_true[start_id:start_id+batch_size] = result[3]
    start_id += batch_size

  # Metrics
  # f1 score depending on number of classes
  if data_class.num_classes == 2:
    # If only 2 classes, then one is positive, and average is binary
    pos_label = np.argmax(data_class.sense_to_one_hot['positive'])
    average = 'binary'
    f1_micro = f1_score(y_true, y_pred, pos_label=pos_label, average='binary')
  else:
    # If multiclass, no positive labels
    f1_micro = f1_score(y_true, y_pred, average='micro')

  acc = accuracy_score(y_true, y_pred)
  # f1_conll = data_class.conll_f1_score(y_pred, data.orig_disc, data.path_source)
  f1_conll =f1_micro
  return f1_micro, f1_conll, acc

def test_set_decoder_loss(sess, model, batch_size, num_batches, prog):
  """ Get the total loss for the entire batch """
  data = [x_test_enc, x_test_dec, classes_test, enc_len_test,
          dec_len_test, dec_test, dec_mask_test]
  fetch = [model.batch_size, model.class_cost]
  losses = np.zeros(num_batches_test) # to average the losses
  batch_w = np.zeros(num_batches_test) # batch weight
  batch_results = call_model(sess, model, data, fetch, batch_size, num_batches,
                              keep_prob=1, shuffle=False)
  for i, result in enumerate(batch_results):
    # Keep track of losses to average later
    cur_b_size = result[0]
    losses[i] = result[1]
    batch_w[i] = cur_b_size / len(x_test_enc)

  # Average across batches
  av = np.average(losses, weights=batch_w)
  prog.print_eval('decoder loss', av)

def language_model_class_loss():
  """ Try all label conditioning for eval dataset
  For each sample, get the perplexity when conditioning on all classes and set
  the label with argmin. Check accuracy and f1 score of classification
  """
  # To average the losses
  log_prob = np.zeros((len(classes_test), data_class.num_classes), dtype=np.float32)
  for k, v in data_class.sense_to_one_hot.items(): # loop over all classes
    class_id = np.argmax(v)
    classes = np.array([v])
    classes = np.repeat(classes, len(classes_test), axis=0)
    assert classes_test.shape == classes.shape

    data = [x_test_enc, x_test_dec, classes, enc_len_test,
          dec_len_test, dec_test, dec_mask_test]
    fetch = [model.batch_size, model.generator_loss, model.softmax_logits,
              model.dec_targets]
    batch_results = call_model(sess, model, data, fetch, batch_size,
                               num_batches_test, keep_prob=1, shuffle=False)
    j = 0
    for result in batch_results:
      cur_b_size = result[0]
      # loss = result[1]

      # Get the probability of the words we want
      targets = result[3]
      probs = result[2] # [batch_size, time step, vocab_size]
      targets = targets[:,0:probs.shape[1]] # ignore zero pads
      I,J=np.ix_(np.arange(probs.shape[0]),np.arange(probs.shape[1]))
      prob_vocab = probs[I,J,targets]
      # Get the sum log across all words per sample
      sum_log_prob = np.sum(np.log(prob_vocab), axis=1) # [batch_size,]
      # Assign the sum log prob to the correct class column
      log_prob[j:j+cur_b_size, class_id] = sum_log_prob
      j += cur_b_size

  predictions = np.argmax(log_prob, axis=1) # get index of most probable
  gold = np.argmax(classes_test, axis=1) # get index of one hot class
  correct = predictions == gold # compare how many match
  accuracy = np.sum(correct)/len(correct)
  prog.print_class_eval(accuracy)

###############################################################################
# Hyperparameters
###############################################################################
# Default params
hyperparams = {
  'batch_size'       : settings['hp']['batch_size'],       # training batch size
  'cell_units'       : settings['hp']['cell_units'],       # hidden layer size
  'dec_out_units'    : settings['hp']['dec_out_units'],    # output from decoder
  'num_layers'       : settings['hp']['num_layers'],       # not used
  'keep_prob'        : settings['hp']['keep_prob'],        # dropout keep prob
  'nb_epochs'        : settings['hp']['nb_epochs'],        # max training epochs
  'early_stop_epoch' : settings['hp']['early_stop_epoch'], # stop after n epochs
  'cell_type'        : settings['hp']['cell_type'],
  'bidirectional'    : settings['hp']['bidirectional'],
  'attention'        : settings['hp']['attention'],
  'class_over_sequence' : settings['hp']['class_over_sequence'],
  'hidden_size'      : settings['hp']['hidden_size'],
  'fc_num_layers'    : settings['hp']['fc_num_layers']
}
# Params configured for tuning
search_space = {
  'batch_size'    : hp.choice('batch_size', range(32, 128)),
  'cell_units'    : hp.choice('cell_units', range(4, 150)), # hidden layer size
  'dec_out_units' : hp.choice('dec_out_units', range(4, 100)), #output from dec
  'num_layers'    : hp.choice('num_layers', range(1, 10)),  # not used
  'keep_prob'     : hp.uniform('keep_prob', 0.1, 1),  #dropout keep probability
  'hidden_size'   : hp.choice('hidden_size', range(60, 300)), # fc layer size
  'fc_num_layers' : hp.choice('fc_num_layers', range(1, 10)), #num of fc layers
}
###############################################################################
# Train
###############################################################################
current_trial = 0
# Launch training
def train(params):
  global current_trial
  current_trial += 1
  print('-' * 79)
  print('Current trial: {}'.format(current_trial))
  print('-' * 79)
  # tf.reset_default_graph() # reset the graph for each trial
  batch_size = params['batch_size']
  train_set = dataset_dict['training_set']
  val_set = dataset_dict['validation_set']
  prog = Progress(batches=train_set.num_batches(batch_size), progress_bar=True,
                  bar_length=10)
  met = Metrics(monitor="val_f1")
  cb = Callback(params['early_stop_epoch'], met, prog)

  # Print some info
  pprint(params)
  # Dataset info
  for name, dataset in dataset_dict.items():
    print('Size of {} : {}'.format(name, dataset.size()))
  print('Number of classes: {}'.format(data_class.num_classes))

  # Save trials along the way
  pickle.dump(trials, open("trials.p","wb"))

  # Declare model with hyperparams
  with tf.Graph().as_default(), tf.Session() as sess:
    model = EncDec(\
            num_units=params['cell_units'],
            dec_out_units=params['dec_out_units'],
            max_seq_len=max_arg_len,
            num_classes=data_class.num_classes,
            embedding=embedding,
            emb_dim=embedding.shape[1],
            cell_type=params['cell_type'],
            bidirectional=params['bidirectional'],
            emb_trainable=params['emb_trainable'],
            class_over_sequence=params['class_over_sequence'],
            hidden_size=params['hidden_size'],
            fc_num_layers=params['fc_num_layers']
            )

    writer = tf.summary.FileWriter('logs', sess.graph)
    # Start training
    tf.global_variables_initializer().run()
    for epoch in range(params['nb_epochs']):
      prog.epoch_start()

      # Training set
      train_one_epoch(sess, train_set, model, params['keep_prob'],
                  batch_size, train_set.num_batches(batch_size), prog, writer)

      # Validation Set
      prog.print_cust('|| {} '.format(val_set.short_name))
      _, f1, accuracy = classification_f1(
              sess, val_set, model, batch_size, val_set.num_batches(batch_size))
      met.update(val_set.short_name + '_f1', f1)
      met.update(val_set.short_name + '_acc', accuracy)
      prog.print_eval('acc', accuracy)
      prog.print_eval('f1', f1)

      for k, dataset in dataset_dict.items():
        if k == "training_set": continue # skip training, already done
        if k == "validation_set": continue # skip validation, already done

        # Other sets
        prog.print_cust('|| {} '.format(dataset.short_name))
        _, f1, accuracy = classification_f1(
            sess, dataset, model, batch_size, dataset.num_batches(batch_size))
        met.update(dataset.short_name + '_f1', f1)
        met.update(dataset.short_name + '_acc', accuracy)
        prog.print_eval('acc', accuracy)
        prog.print_eval('f1', f1)

      if cb.early_stop() == True: break
      prog.epoch_end()
    print(met)
    prog.epoch_end()

  # Results of this trial
  results = {
      'loss'              : -met.metric_best, # required by hyperopt
      'status'            : STATUS_OK, # required by hyperopt
      'metrics'           : met.metric_dict,
      'params'            : params
  }
  # dump results
  if 'file_save' in params:
    with codecs.open(params['file_save'], mode='a', encoding='utf8') as output:
      json.dump(results, output)
      output.write('\n')
  # Return for hyperopt
  return results

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__,
                          formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('--trials', default=1, type=int, help='Max number of trials')
  parser.add_argument('--search_param', help='Hyperparam search over this param')
  parser.add_argument('--file_save', help='Save results of search to this json')
  args = parser.parse_args()

  trials = Trials()
  params = hyperparams
  if args.search_param: params[args.search_param] = search_space[args.search_param]
  # params['trials'] = trials
  if args.file_save: params['file_save'] = args.file_save

  params['dataset_name'] = settings['use_dataset']
  params['relation'] = settings[dataset_name]['this_relation']
  params['random_init_unknown'] = settings['random_init_unknown']
  params['max_vocab'] = settings['max_vocab']
  params['emb_trainable'] = settings['emb_trainable']
  max_evals = args.trials
  best = fmin(train, params, algo=tpe.suggest, max_evals=max_evals, trials=trials)
  print('best: ')
  print(best)
  # pickle.dump(trials, open("trials.p","wb"))

