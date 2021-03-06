import re
import numpy as np
import pickle
import json
from collections import Counter
import codecs
from pprint import pprint
import random
import sys
import os.path
from conll_utils.scorer import f1_non_explicit

dtype='int32' # default numpy int dtype
np.random.seed(1)

# TODO : checkout tf.contrib preprocessing for tokenization
class Data():
  def __init__(self, short_name, path_source):
    self.short_name = short_name
    self._path_source = path_source
    self._x = None
    self._classes = None
    self._seq_len = [] # list of tuple(len_arg1, len_arg2)
    self._decoder_target = []
    self.orig_disc = [] # the original discourse, list from json to dict
    self._sense_to_one_hot = {} # maps sense string to its encoding

  @property
  def path_source(self):
    """ Path of original json file """
    return self._path_source

  @path_source.setter
  def path_source(self, value):
    self._path_source = value

  @property
  def decoder_target(self):
    return self._decoder_target

  @decoder_target.setter
  def decoder_target(self, value):
    self._decoder_target = value

  @property
  def classes(self):
    return self._classes

  @classes.setter
  def classes(self, value):
    self._classes = value

  @property
  def seq_len(self):
    return self._seq_len

  @seq_len.setter
  def seq_len(self, value):
    self._seq_len = value

  @property
  def encoder_input(self):
    """ Encoder input """
    return self._x[0]

  @property
  def decoder_input(self):
    """ Decoder input """
    return self._x[1]

  @property
  def x(self):
    return self._x

  @x.setter
  def x(self, value):
    self._x = value

  @property
  def sense_to_one_hot(self):
    return self._sense_to_one_hot

  @sense_to_one_hot.setter
  def sense_to_one_hot(self, value):
    self._sense_to_one_hot = value

  @property
  def seq_len_encoder(self):
    """ Sequence length for encoder input """
    return self._seq_len[:,0]

  @property
  def seq_len_decoder(self):
    """ Sequence length for decoder input """
    return self._seq_len[:,1]

  @property
  def num_classes(self):
    """ Number of unique classes """
    return self.classes.shape[1]

  def size(self):
    """ Samples in dataset """
    return len(self.x[0])

  def num_batches(self, batch_size):
    return self.size()//batch_size+(self.size()%batch_size>0)



class MiniData():
  """ Inherits Data properties indirectly.
  Allows Data object properties to be automatically indexed. If the property
  is not indexable, returns error.
  """
  def __init__(self, data, indices):
    """  data must be a Data object """
    self.data = data
    self.indices = indices

  def __getattr__(self, name):
    """ Returns sliced property from data object """
    return self.data.__getattribute__(name)[self.indices]

def make_batches(data, batch_size, num_batches, shuffle=True):
  """ Yields the data object with all properties sliced """
  data_size = len(data.encoder_input)
  indices = np.arange(0, data_size)
  if shuffle: np.random.shuffle(indices)
  for batch_num in range(num_batches):
    start_index = batch_num * batch_size
    end_index = min((batch_num + 1) * batch_size, data_size)
    new_indices = indices[start_index:end_index]
    yield MiniData(data, new_indices)

class Preprocess():
  def __init__(self,
        dataset_name, # which dataset from settings file
        max_arg_len, # max length of each argument
        maxlen, # maximum total length of input
        settings, # settings file as dict
        relation=None, # include only this relation
        max_vocab=None, # limit vocab size
        split_input=True, # boolean, split input into separate numpy arrays
        decoder_targets=False, # second arg without bos
        pad_tag = "<pad>", # padding tag
        unknown_tag = "<unk>", # tag for unknown
        bos_tag = None, # beginning of sequence tag
        eos_tag = None, # end of sequence tag
        vocab=None, # If none, will create the vocab
        inv_vocab=None): # If none, generates inverse vocab

    if relation == "all":
      self.relation = None
    else:
      self.relation = relation

    dataset=settings[dataset_name]

    # self.train_file   = training_set
    # self.val_file     = validation_set
    self.max_arg_len  = max_arg_len
    self.vocab        = None # set in method get_data
    self.inv_vocab    = inv_vocab # set in method get_data
    self.total_tokens = 0
    self.pad_tag      = pad_tag
    self.unknown_tag  = unknown_tag
    self.bos_tag      = bos_tag
    self.eos_tag      = eos_tag
    self.maxlen       = maxlen
    self.split_input  = split_input
    # Sense mapping dict, or list of dicts
    mapping_path=dataset["mapping"]
    self.mapping_sense    = self.get_output_mapping(mapping_path)
    self.sense_to_one_hot = self.get_one_hot_dicts(self.mapping_sense)
    self.int_to_sense     = self.get_int_to_sense_dict(self.sense_to_one_hot)
    self.num_classes      = self.get_class_counts(self.mapping_sense)
    # array shape [samples, 1], or [samples,2] if split
    self.weights_cross_entropy = None


    # Set what to process
    label_key = dataset["label_key"]
    self.data_collect = {}
    for k, v in dataset['datasets'].items():
      self.data_collect[k] = Data(v["short_name"], v["path"])

    # If max vocab
    if max_vocab is not None:
      train_path = self.data_collect['training_set'].path_source
      train_vocab = self.most_common_words(train_path, max_vocab, self.relation)
    else:
      train_vocab = None

    # Tokenize and pad
    for data in self.data_collect.values():
      data.x, data.classes, data.seq_len, data.decoder_target, data.orig_disc=\
            self.load_from_file(data.path_source, self.max_arg_len, label_key,
              self.relation, train_vocab)

      # Array with elements arg1 length, arg2 length
      data.seq_len = np.array(data.seq_len, dtype=dtype)

      # Map original sense (y value) to one hot output or multiple outputs (list)
      # These are already numpy arrays
      data.classes = self.set_output_for_network(data.classes)

      # Pad input according to split
      data.x = self.pad_input(data.x, data.seq_len, self.split_input)
      data.decoder_target = self.pad_input(data.decoder_target, split=False)

      data.sense_to_one_hot = self.sense_to_one_hot
    # self.weights_cross_entropy = (np.sum(y_train, axis=0)/np.sum(y_train))

    # Create vocab for all data
    if self.vocab == None:
      self.vocab, self.inv_vocab = self.create_vocab(self.data_collect, max_vocab)
    self.total_tokens = len(self.vocab)

    # Integerize x and decoder targets, and make numpy arrays
    for k, data in self.data_collect.items():
      # Integerize
      data.x = self.integerize(data.x, self.vocab)
      data.decoder_target = self.integerize(data.decoder_target, self.vocab)

      # Make numpy
      data.x = np.array(data.x)
      data.decoder_target = np.array(data.decoder_target)

      # Split the input between arguments if so desired
      if self.split_input:
        data.x = self.split_x(data.x)

  def set_output_for_network(self, y):
    """ Returns single list of y values, or multiple lists if multiple lists
    if the network has multiples outputs

    Return will depend on mapping_sense class param. If single dictionary,
    then returns single output. If list of multiple dictionaries, then returns
    list of outputs
    """
    if type(self.mapping_sense) is list:
      outputs = [] # list of list of y values for several network outputs
      for i, _ in enumerate(self.mapping_sense):
        labels = []
        for label in y:
          mapping = self.mapping_sense[i] # To map original label to new label
          one_hot_dict = self.sense_to_one_hot[i] # One hot of new label
          label = one_hot_dict[mapping[label]]
          labels.append(label)
        labels = np.array(labels) # swap to numpy array
        outputs.append(labels)
      return outputs
    else:
      outputs = [] # single list of y
      for label in y:
        label = self.sense_to_one_hot[self.mapping_sense[label]]
        outputs.append(label)
      outputs = np.array(outputs)
      return outputs

  def get_output_mapping(self, mapping_path):
    """ Returns single dict, or list of dicts of mapping"""

    if type(mapping_path) is list:
      maps = []
      for path in mapping_path:
        maps.append(self.dict_from_json(path))
      return maps
    else:
      return self.dict_from_json(mapping_path)

  def get_class_counts(self, mapping_sense):
    if type(mapping_sense) is list:
      classes = []
      for mappings in mapping_sense:
        classes.append(len(set(mappings.values())))
      return classes
    else:
      return len(set(mapping_sense.values()))

  def get_one_hot_dicts(self, mapping_sense):
    if type(mapping_sense) is list:
      one_hot = []
      for mapping in mapping_sense:
        senses = set(mapping.values())
        one_hot.append(self.one_hot_dict(senses))
      return one_hot
    else:
      return self.one_hot_dict(set(mapping_sense.values()))

  def get_int_to_sense_dict(self, sense_to_one_hot):
    """ Return dict mapping integer to label based on one hot dict"""
    int_to_sense = {}
    for k, v in sense_to_one_hot.items():
      key = np.argmax(v)
      int_to_sense[key] = k
    return int_to_sense

  def one_hot_dict(self,senses):
    """Return dictionary of one-hot encodings of list of items"""
    # Base vector, all zeros
    base = [0] * len(senses)
    embedding = {}
    for i, x in enumerate(senses):
      emb = base[:]
      emb[i] = 1
      embedding[x] = emb
    return embedding

  def dict_from_json(self, file_path):
    """ Load dictionary from a json file """
    with codecs.open(file_path, encoding='utf-8') as f:
      dictionary = json.load(f)
    return dictionary

  def split_x(self, x):
    x_1 = x[:,:self.max_arg_len]
    x_2 = x[:,self.max_arg_len:]
    x   = [x_1,x_2]
    return x

  def integerize(self, x, vocab):
    """ Swap all tokens for their integer values based on vocab """
    x_new = []
    for sample in x:
      tokens = []
      for word in sample:
        if word not in vocab:
          tokens.append(vocab[self.unknown_tag])
        else:
          tokens.append(vocab[word])
      x_new.append(tokens)
    return x_new

  def pad_input(self, x, arg_len=None, split=False):
    x_new = []
    for i, sample in enumerate(x):
      # Pad end of individual arguments
      if split:
        arg1_len = arg_len[i][0]
        arg2_len = arg_len[i][1]
        pad1     = self.max_arg_len - arg1_len
        pad2     = self.max_arg_len - arg2_len
        sample   = sample[:arg1_len] + [self.pad_tag] * pad1 +\
                   sample[arg1_len:] + [self.pad_tag] * pad2
        x_new.append(sample)

      # Or pad only the end of the whole input
      else:
        pad = self.max_arg_len - len(sample)
        sample.extend([self.pad_tag]*pad)
        x_new.append(sample)
    return x_new

  def load_from_file(self, path, max_arg_len, label_name, relation=None,
                    max_vocab=None):
    """ Parse the input
    Args:
      max_vocab: if given, only these tokens considered
    Returns:
      x : list of tokenized discourse text
      y : list of labels
      arg_len : list of tuples (arg1_length, arg2_length)
      discourse_list : if !None, saves discourse info to this list
    """
    x = list(); y = list(); arg_len=list(); decoder_targets=list();
    discourse_list = list()
    with codecs.open(path, encoding='utf8') as pdfile:
      invalid = 0
      for line in pdfile:
        j = json.loads(line)

        # Maybe exclude this relation
        if relation is not None:
          if j['Relation'] != relation: continue

        discourse_list.append(j)
        arg1 = clean_str(j['Arg1']['RawText'])
        arg2 = clean_str(j['Arg2']['RawText'])

        # Consider only max_vocab tokens
        if max_vocab is not None:
          arg1 = [x for x in arg1 if x in max_vocab]
          arg2 = [x for x in arg2 if x in max_vocab]

        arg1 = arg1[:self.max_arg_len]
        if self.bos_tag:
          arg2.insert(0, self.bos_tag)
        arg2 = arg2[:self.max_arg_len]
        dec_target = arg2[1:]
        dec_target.append(self.eos_tag)
        decoder_targets.append(dec_target)

        l1 = len(arg1)
        l2 = len(arg2)
        if l1 < 1 or l2 < 1:
          invalid += 1
          continue

        arg1.extend(arg2)
        # Return original sense, mapping done later
        if type(j[label_name]) == list:
          label = j['Sense'][0]
        else:
          label = j[label_name]

        # Add sample to list of data
        x.append(arg1)
        y.append(label)
        arg_len.append((l1,l2))
    print("There were ", invalid, " invalid discourses in file:")
    print(path)
    return x, y, arg_len, decoder_targets, discourse_list

  def add_tags(self, seq_list):
    """ Adds beginning and/or end of sequence tags if set """
    if self.bos_tag:
      seq_list.insert(0, self.bos_tag)
    if self.eos_tag:
      seq_list.append(self.eos_tag)
    return seq_list

  def most_common_words(self, path, max_vocab, relation=None):
    words = []
    with codecs.open(path, encoding='utf8') as pdfile:
      for line in pdfile:
        j = json.loads(line)
        # Maybe exclude this relation
        if relation is not None:
          if j['Relation'] != relation: continue
        arg1 = clean_str(j['Arg1']['RawText'])
        arg2 = clean_str(j['Arg2']['RawText'])
        words.extend(arg1)
        words.extend(arg2)
    count = Counter(words) # word count
    vocab = [x[0] for x in count.most_common(max_vocab)]
    return vocab

  def create_vocab(self, data_collect, max_vocab):
    """ Create a dictionary of words to int, and the reverse

    You'll want to save this, required for model restore
    """
    # Get raw data
    x_list = [data.x for data in data_collect.values()]
    sample_count = sum([len(x) for x in x_list])
    words = []
    for data in x_list:
      words.extend([word for sublist in data for word in sublist])
    words.extend([self.eos_tag] * sample_count) # silly hack to add tag
    count = Counter(words) # word count
    # Vocab in descending order
    inv_vocab = [x[0] for x in count.most_common(max_vocab)]
    if self.unknown_tag:
      inv_vocab.insert(0, self.unknown_tag)
    # Vocab with index position instead of word
    vocab = {x: i for i, x in enumerate(inv_vocab)}
    return vocab, inv_vocab

  def conll_f1_score(self, predictions, orig_disc, gold_path):
    self.save_to_conll_format('tmp.json', predictions, orig_disc)
    precision, recall, f1 = f1_non_explicit('tmp.json', gold_path)
    return f1

  def save_to_conll_format(self, path, predictions, discourse, append_file=False):
    """ Saves as json in conll format
    Args:
      path : path where to write the json
      predictions : sense prediction from the neural network, numpy array
      discourse : list of dicourse dictionary. Index of discourse in this list
            must match index of discourse in predictions array

    Writes Json file, where each line is:
    {   Arg1: {TokenList: [275, 276, 277], RawText: "raw text"},
        Arg2: {TokenList: [301, 302, 303], RawText: "raw text"},
        Connective: {TokenList: []},
        DocID: 'wsj_1000',
        Sense: ['Expansion.Conjunction'],
        Type: 'Implicit'
    }
    """

    if append_file == False:
      # Remove file if exists
      if os.path.isfile(path):
        os.remove(path)

    # Loop through results
    with codecs.open(path, mode='a', encoding='utf8') as pdtb:
      for i, disc in enumerate(discourse):
        sense_id = int(predictions[i])
        disc['Sense'] = [self.int_to_sense[sense_id]]
        json.dump(disc, pdtb) #indent to add to new line
        pdtb.write('\n')
    # print("\nSaved results as CoNLL json to here: ", path)

def clean_str(string):
  """
  Clean string, return tokenized list
  """
  string = re.sub(r"[^A-Za-z0-9(),!?\'\`]", " ", string)
  string = re.sub(r"\'s", " \'s", string)
  string = re.sub(r"\'ve", " \'ve", string)
  string = re.sub(r"n\'t", " n\'t", string)
  string = re.sub(r"\'re", " \'re", string)
  string = re.sub(r"\'d", " \'d", string)
  string = re.sub(r"\'ll", " \'ll", string)
  string = re.sub(r",", " , ", string)
  string = re.sub(r"!", " ! ", string)
  string = re.sub(r"\(", " \( ", string)
  string = re.sub(r"\)", " \) ", string)
  string = re.sub(r"\?", " \? ", string)
  string = re.sub(r"\s{2,}", " ", string)

  return string.strip().lower().split()

def settings(path):
  """ Returns settings dictionary """
  with codecs.open(path, encoding='utf-8') as f:
    s= json.load(f)

  s['random_init_unknown'] = parse_bool(s['random_init_unknown'])
  s['max_vocab'] = parse_int(s['max_vocab'])
  s['tensorboard_write'] = parse_bool(s['tensorboard_write'])
  s['split_input'] = parse_bool(s['split_input'])
  s['save_alignment_history'] = parse_bool(s['save_alignment_history'])

  hparams = HParams(
    batch_size          = parse_int(s['hp']['batch_size']),
    cell_units          = parse_int(s['hp']['cell_units']),
    cell_type           = s['hp']['cell_type'],
    optimizer           = s['hp']['optimizer'],
    dec_out_units       = parse_int(s['hp']['dec_out_units']),
    num_layers          = parse_int(s['hp']['num_layers']),
    keep_prob           = parse_float(s['hp']['keep_prob']),
    nb_epochs           = parse_int(s['hp']['nb_epochs']),
    early_stop_epoch    = parse_int(s['hp']['early_stop_epoch']),
    bidirectional       = parse_bool(s['hp']['bidirectional']),
    l_rate              = parse_float(s['hp']['l_rate']),
    attention           = parse_bool(s['hp']['attention']),
    class_over_sequence = parse_bool(s['hp']['class_over_sequence']),
    hidden_size         = parse_int(s['hp']['hidden_size']),
    fc_num_layers       = parse_int(s['hp']['fc_num_layers']),
    max_arg_len         = parse_int(s['hp']['max_arg_len']),
    max_seq_len         = parse_int(s['hp']['max_arg_len']),
    maxlen              = parse_int(s['hp']['max_arg_len'])*2,
    unknown_tag         = s['hp']['unknown_tag'],
    pad_tag             = s['hp']['pad_tag'],
    bos_tag             = s['hp']['bos_tag'],
    eos_tag             = s['hp']['eos_tag'],
    emb_trainable       = s['hp']['emb_trainable']
  )

  return hparams, s

def parse_bool(val):
  if val == "True":
    return True
  if val == "False":
    return False
  if val == "None":
    return None

def parse_int(val):
  if val == "None":
    return None
  else:
    return int(val)

def parse_float(val):
  if val == "None":
    return None
  else:
    return float(val)

class HParams():
  def __init__(self, **kwargs):
    for k, v in kwargs.items():
      setattr(self, k, v)

  def update(self, **kwargs):
    for k, v in kwargs.items():
      setattr(self, k, v)

def get_data(hparams, settings):
  """
  Convenience function to create the datasets needed
  Args:
    hparams: HParam object
    settings: settings dictionary
  Returns:
    A dictionary of train/validation/test sets, and possibly blind dataset.
    Dictionary {k: v} is {dataset name: Data object}
  """
  dataset_name = settings['use_dataset']
  data_class = Preprocess(
              dataset_name = dataset_name,
              relation = settings[dataset_name]['this_relation'],
              max_vocab = settings['max_vocab'],
              max_arg_len= hparams.max_arg_len,
              maxlen=hparams.maxlen,
              settings=settings,
              split_input=settings['split_input'],
              pad_tag = hparams.pad_tag,
              unknown_tag = hparams.unknown_tag,
              bos_tag = hparams.bos_tag,
              eos_tag = hparams.eos_tag)
  vocab = data_class.vocab
  inv_vocab = data_class.inv_vocab

  # Once vocab and inv_vocab created, update hparams with their index values
  hparams.update(
    num_classes = data_class.num_classes,
    start_token = vocab[hparams.bos_tag],
    end_token = vocab[hparams.eos_tag],
  )

  # Data sets as Data objects
  dataset_dict = data_class.data_collect
  return dataset_dict, vocab, inv_vocab

def alignment(enc_in, dec_in, alignment, inv_vocab):
  """ process data and save alignments """
  # Alignment is time major, make batch major
  alignment  = np.transpose(alignment, (1,0,2))
  # Trim blank out
  max_steps_dec = alignment.shape[1]
  dec_in = dec_in[:,:max_steps_dec]
  # Convert to list of tokens
  enc_in = enc_in.tolist()
  for i, seq in enumerate(enc_in):
    enc_in[i] = [inv_vocab[x] for x in seq]
  dec_in = dec_in.tolist()
  for i, seq in enumerate(dec_in):
    dec_in[i] = [inv_vocab[x] for x in seq]

  # Return list of dicts
  alignment_ls = []
  path = 'tmp.json'
  for i, disc in enumerate(enc_in):
    arg1 = enc_in[i]
    arg2 = dec_in[i]
    align = alignment[i]
    sample = {'arg1': arg1, 'arg2': arg2, 'alignment': align}
    alignment_ls.append(sample)
  return alignment_ls


