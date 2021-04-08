#!/usr/bin/env python
# coding: utf-8

from __future__ import absolute_import, division, print_function

import logging
import math
import os

import numpy as np
import sklearn
import torch
import wandb
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    matthews_corrcoef,
)
from sklearn.preprocessing import MultiLabelBinarizer
from torch.nn import CrossEntropyLoss

from transformers import (
    AdamW,
    get_linear_schedule_with_warmup,
)


class SeqTaggingTrainer:
    def __init__(self, args, device, model, train_dl=None, test_dl=None, test_examples=None, tokenizer=None):
        self.args = args
        self.device = device

        # set data
        self.num_labels = args.num_labels
        self.set_data(train_dl, test_dl, test_examples)

        # model
        self.model = model
        self.model.to(self.device)

        # training results
        self.results = {}
        self.best_accuracy = 0.0

        self.tokenizer = tokenizer
        self.pad_token_label_id = self.args.pad_token_label_id

    def set_data(self, train_dl, test_dl=None, test_examples=None):
        # Used for fedtrainer
        self.train_dl = train_dl
        self.test_dl = test_dl
        self.test_examples = test_examples

    def train_model(self):

        # build optimizer and scheduler
        iteration_in_total = len(
            self.train_dl) // self.args.gradient_accumulation_steps * self.args.num_train_epochs
        optimizer, scheduler = self.build_optimizer(self.model, iteration_in_total)

        # training result
        global_step = 0
        tr_loss, logging_loss = 0.0, 0.0
        for epoch in range(0, self.args.num_train_epochs):

            self.model.train()

            for batch_idx, batch in enumerate(self.train_dl):

                batch = tuple(t for t in batch)
                # dataset = TensorDataset(all_guid, all_input_ids, all_input_mask, all_segment_ids, all_label_ids)
                x = batch[1].to(self.device)
                labels = batch[4].to(self.device)

                # (loss), logits, (hidden_states), (attentions)
                output = self.model(x)
                logits = output[0]
                loss_fct = CrossEntropyLoss()
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))

                # model outputs are always tuple in pytorch-transformers (see doc)
                # loss = outputs[0]
                # logging.info(loss)
                current_loss = loss.item()
                logging.info("epoch = %d, batch_idx = %d/%d, loss = %s" % (epoch, batch_idx,
                                                                           len(self.train_dl), current_loss))

                if self.args.gradient_accumulation_steps > 1:
                    loss = loss / self.args.gradient_accumulation_steps

                loss.backward()

                tr_loss += loss.item()
                if (batch_idx + 1) % self.args.gradient_accumulation_steps == 0:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.args.max_grad_norm)
                    optimizer.step()
                    scheduler.step()  # Update learning rate schedule
                    self.model.zero_grad()
                    global_step += 1

                    if self.args.evaluate_during_training and (self.args.evaluate_during_training_steps > 0
                                                               and global_step % self.args.evaluate_during_training_steps == 0):
                        results, _, _ = self.eval_model(epoch, global_step)
                        logging.info(results)

                if self.args.is_debug_mode == 1 and global_step > 3:
                    break
        results, _, _ = self.eval_model(self.args.num_train_epochs-1, global_step)
        logging.info(results)
        return global_step, tr_loss / global_step

    def eval_model(self, epoch, global_step):
        results = {}

        eval_loss = 0.0
        nb_eval_steps = 0
        n_batches = len(self.test_dl)
        # TODO: check the value of len(self.test_examples)
        test_sample_len = len(self.test_examples)
        pad_token_label_id = self.pad_token_label_id
        

        preds = None
        out_label_ids = None

        self.model.eval()
        logging.info("len(test_dl) = %d, n_batches = %d" % (len(self.test_dl), n_batches))
        for i, batch in enumerate(self.test_dl):
            batch = tuple(t for t in batch)
            with torch.no_grad():
                sample_index_list = batch[0].to(self.device).cpu().numpy()

                if i == len(self.test_dl) - 1:
                    logging.info(batch)
                x = batch[1].to(self.device)
                labels = batch[4].to(self.device)

                output = self.model(x)
                logits = output[0]

                loss_fct = CrossEntropyLoss()
                loss = loss_fct(logits.view(-1, self.num_labels), labels.view(-1))
                eval_loss += loss.item()
                # logging.info("test. batch index = %d, loss = %s" % (i, str(eval_loss)))

            nb_eval_steps += 1
            start_index = self.args.eval_batch_size * i

            end_index = start_index + self.args.eval_batch_size if i != (n_batches - 1) else test_sample_len
            logging.info("batch index = %d, start_index = %d, end_index = %d" % (i, start_index, end_index))

            if preds is None:
                preds = logits.detach().cpu().numpy()
                out_label_ids = batch[4].detach().cpu().numpy()
                out_input_ids = batch[1].detach().cpu().numpy()
                out_attention_mask = batch[2].detach().cpu().numpy()
            else:
                preds = np.append(preds, logits.detach().cpu().numpy(), axis=0)
                out_label_ids = np.append(out_label_ids, batch[4].detach().cpu().numpy(), axis=0)
                out_input_ids = np.append(out_input_ids, batch[1].detach().cpu().numpy(), axis=0)
                out_attention_mask = np.append(
                    out_attention_mask, batch[2].detach().cpu().numpy(), axis=0,
                )


        eval_loss = eval_loss / nb_eval_steps

        token_logits = preds
        preds = np.argmax(preds, axis=2)

        label_map = {i: label for i, label in enumerate(self.args.labels_list)}

        out_label_list = [[] for _ in range(out_label_ids.shape[0])]
        preds_list = [[] for _ in range(out_label_ids.shape[0])]

        for i in range(out_label_ids.shape[0]):
            for j in range(out_label_ids.shape[1]):
                if out_label_ids[i, j] != pad_token_label_id:
                    out_label_list[i].append(label_map[out_label_ids[i][j]])
                    preds_list[i].append(label_map[preds[i][j]])

        word_tokens = []
        for i in range(len(preds_list)):
            w_log = self._convert_tokens_to_word_logits(
                out_input_ids[i], out_label_ids[i], out_attention_mask[i], token_logits[i],
            )
            word_tokens.append(w_log)

        model_outputs = [[word_tokens[i][j] for j in range(len(preds_list[i]))] for i in range(len(preds_list))]

        result, wrong = self.compute_metrics(preds_list, out_label_list, self.test_examples)
        result["eval_loss"] = eval_loss
        results.update(result)

        os.makedirs(self.args.output_dir, exist_ok=True)
        output_eval_file = os.path.join(self.args.output_dir, "eval_results.txt")
        with open(output_eval_file, "w") as writer:
            for key in sorted(result.keys()):
                writer.write("{} = {}\n".format(key, str(result[key])))
        if result["acc"] > self.best_accuracy:
            self.best_accuracy = result["acc"]
        logging.info("best_accuracy = %f" % self.best_accuracy)

        # TODO: only do when wandb is enabled
        # wandb.log({"Evaluation Accuracy (best)": self.best_accuracy, "step": global_step})
        # wandb.log({"Evaluation Accuracy": result["acc"], "step": global_step})
        # wandb.log({"Evaluation Loss": result["eval_loss"], "step": global_step})

        self.results.update(result)
        logging.info(self.results)

        return result, model_outputs, wrong

    def compute_metrics(self, preds, labels, eval_examples=None):
        assert len(preds) == len(labels)

        binarizer = MultiLabelBinarizer()
        labels_binary = binarizer.fit_transform(labels)
        preds_binary = binarizer.transform(preds)

        extra_metrics = {}
        extra_metrics["acc"] = sklearn.metrics.accuracy_score(labels_binary, preds_binary)
        mismatched = labels != preds

        logging.info(111)
        logging.info(mismatched)

        if eval_examples:
            wrong = [i for (i, v) in zip(eval_examples, mismatched) if v.any()]
        else:
            wrong = ["NA"]

        mcc = matthews_corrcoef(labels, preds)

        tn, fp, fn, tp = confusion_matrix(labels, preds, labels=[0, 1]).ravel()
        return (
            {**{"mcc": mcc, "tp": tp, "tn": tn, "fp": fp, "fn": fn}, **extra_metrics},
            wrong,
        )

    def build_optimizer(self, model, iteration_in_total):
        warmup_steps = math.ceil(iteration_in_total * self.args.warmup_ratio)
        self.args.warmup_steps = warmup_steps if self.args.warmup_steps == 0 else self.args.warmup_steps
        logging.info("warmup steps = %d" % self.args.warmup_steps)
        # optimizer = torch.optim.Adam(self._get_optimizer_grouped_parameters(), lr=self.args.learning_rate, betas=(0.9, 0.999), weight_decay=0.01)
        optimizer = AdamW(model.parameters(), lr=self.args.learning_rate,
                          eps=self.args.adam_epsilon)
        scheduler = get_linear_schedule_with_warmup(
            optimizer, num_warmup_steps=self.args.warmup_steps, num_training_steps=iteration_in_total
        )
        return optimizer, scheduler

    def _convert_tokens_to_word_logits(self, input_ids, label_ids, attention_mask, logits):

        ignore_ids = [
            self.tokenizer.convert_tokens_to_ids(self.tokenizer.pad_token),
            self.tokenizer.convert_tokens_to_ids(self.tokenizer.sep_token),
            self.tokenizer.convert_tokens_to_ids(self.tokenizer.cls_token),
        ]

        # Remove unuseful positions
        masked_ids = input_ids[(1 == attention_mask)]
        masked_labels = label_ids[(1 == attention_mask)]
        masked_logits = logits[(1 == attention_mask)]
        for id in ignore_ids:
            masked_labels = masked_labels[(id != masked_ids)]
            masked_logits = masked_logits[(id != masked_ids)]
            masked_ids = masked_ids[(id != masked_ids)]

        # Map to word logits
        word_logits = []
        tmp = []
        for n, lab in enumerate(masked_labels):
            if lab != self.pad_token_label_id:
                if n != 0:
                    word_logits.append(tmp)
                tmp = [list(masked_logits[n])]
            else:
                tmp.append(list(masked_logits[n]))
        word_logits.append(tmp)

        return word_logits