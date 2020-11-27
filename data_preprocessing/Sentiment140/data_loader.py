import os
import csv
import sys

# add the FedML root directory to the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "../../")))

from data_preprocessing.base.base_raw_data_loader import BaseRawDataLoader
from data_preprocessing.base.base_client_data_loader import BaseClientDataLoader


class RawDataLoader(BaseRawDataLoader):
    def __init__(self, data_path):
        super().__init__(data_path)
        self.task_type = "text_classification"
        self.target_vocab = None
        self.test_file_name = "testdata.manual.2009.06.14.csv"
        self.train_file_name = "training.1600000.processed.noemoticon.csv"

    def data_loader(self):
        if len(self.X) == 0 or len(self.Y) == 0 or self.target_vocab is None:
            X, Y = self.process_data(os.path.join(self.data_path, self.train_file_name))
            train_size = len(X)
            temp = self.process_data(os.path.join(self.data_path, self.test_file_name))
            X.extend(temp[0])
            Y.extend(temp[1])
            self.X, self.Y = X, Y
            train_index_list = [i for i in range(train_size)]
            test_index_list = [i for i in range(train_size, len(self.X))]
            index_list = train_index_list + test_index_list
            self.attributes = {"index_list": index_list, "train_index_list": train_index_list,
                               "test_index_list": test_index_list}
            self.target_vocab = {key: i for i, key in enumerate(set(Y))}
        return {"X": self.X, "Y": self.Y, "target_vocab": self.target_vocab, "task_type": self.task_type,
                "attributes": self.attributes}

    def process_data(self, file_path):
        X = []
        Y = []
        with open(file_path ,"r",newline='',encoding='utf-8',errors='ignore') as csvfile:
            data = csv.reader(csvfile,delimiter=',')
            for line in data:
                X.append(line[5])
                Y.append(line[0])

        return X, Y

class ClientDataLoader(BaseClientDataLoader):

    def __init__(self, data_path, partition_path, client_idx=None, partition_method="uniform", tokenize=False):
        data_fields = ["X", "Y"]
        attribute_fields = ["target_vocab"]
        super().__init__(data_path, partition_path, client_idx, partition_method, tokenize, data_fields,
                         attribute_fields)
        if self.tokenize:
            self.tokenize_data()

    def tokenize_data(self):
        tokenizer = self.spacy_tokenizer.en_tokenizer

        def __tokenize_data(data):
            for i in range(len(data["X"])):
                data["X"][i] = [str(token) for token in tokenizer(data["X"][i])]

        __tokenize_data(self.train_data)
        __tokenize_data(self.test_data)


