from logging import error
import os
import json
import h5py
import string
from numpy.core.arrayprint import repr_format

from data.raw_data_loader.base.base_raw_data_loader import SpanExtractionRawDataLoader

# test script  python test_rawdataloader.py --dataset MRQA --data_dir "../../../../reading_comprehension/" --h5_file_path ./mrqa_data.h5

class RawDataLoader(SpanExtractionRawDataLoader):
    def __init__(self, data_path):
        super().__init__(data_path)
        # i rename some of the file so that they are more distinguishable 
        self.train_file_name = ["HotpotQA.jsonl","NewsQA.jsonl", "SearchQA.jsonl", "NaturalQuestionsShort.jsonl","SQuAD.jsonl" ,"TriviaQA-web.jsonl"]
        self.test_file_name = ["HotpotQA-dev.jsonl","NewsQA-dev.jsonl","SearchQA-dev.jsonl", "NaturalQuestionsShort-dev.jsonl","SQuAD-dev.jsonl","TriviaQA-web-dev.jsonl"]
        self.question_ids = dict()
        self.attributes["train_index_list"] = []
        self.attributes["test_index_list"] = []
        self.attributes['label_index_list'] = []

    def load_data(self):
        if len(self.context_X) == 0 or len(self.question_X) == 0 or len(self.Y) == 0:
            self.attributes["label_index"] = dict() # cannot gather this information if not nessary remove it
            train_size = 0
            test_size = 0
            for train_dataset in self.train_file_name:
                label = train_dataset.split(".")[0]
                train_size += self.process_data_file(os.path.join(self.data_path, train_dataset),label)
            for test_dataset in self.test_file_name:
                label = test_dataset.split("-")[0]
                test_size += self.process_data_file(os.path.join(self.data_path, test_dataset),label)
            self.attributes["train_index_list"] = [i for i in range(train_size)]
            self.attributes["test_index_list"] = [i for i in range(train_size, train_size + test_size)]
            self.attributes["index_list"] = self.attributes["train_index_list"] + self.attributes["test_index_list"]
            assert len(self.attributes['index_list']) == len(self.attributes['label_index_list'])
            print(len( self.attributes["train_index_list"] ))
            print(len(self.attributes["test_index_list"]))
        
    
    def process_data_file(self,file_path,label):
        cnt = 0
        printable = set(string.printable)
        with open(file_path, "r", encoding='utf-8',errors="ignore") as f:
            next(f)
            for line in f:
                paragraph = json.loads(line)
                for question in paragraph['qas']:
                    for answer in question['detected_answers']: # same answer continue or not?
                        assert len(self.context_X) == len(self.question_X) == len(self.Y) == len(self.question_ids)
                        idx = len(self.context_X)
                        self.context_X[idx] =  ''.join(filter(lambda x: x in printable, paragraph['context']))
                        self.question_X[idx] = question['question']
                        start = answer['char_spans'][0][0]
                        end = answer['char_spans'][0][1]
                        self.Y[idx] = (start, end)
                        self.question_ids[idx] = question['qid']
                        self.attributes["label_index_list"].append(label)
                        cnt+= 1
        print("finish loading ",file_path)
        return cnt
    def generate_h5_file(self, file_path):
        f = h5py.File(file_path, "w")
        f["attributes"] = json.dumps(self.attributes)
        for key in self.context_X.keys():
            print(key)
            f["context_X/" + str(key)] = self.context_X[key]
            f["question_X/" + str(key)] = self.question_X[key]
            f["Y/" + str(key)] = self.Y[key]
            f["question_ids/" + str(key)] = self.question_ids[key]
        f.close()



