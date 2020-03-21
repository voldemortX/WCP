# WCP
Source code for "WCP: Worst-Case Perturbations for Semi-Supervised Deep Learning" in CPVR 2020.

## Abstract
In this paper, we present a novel regularization mechanism for training deep networks by minimizing the Worse-Case Perturbation (WCP). It is based on the idea that a robust model is least likely to be affected by small perturbations, such that its output decisions should be as stable as possible on both labeled and unlabeled examples. We will consider two forms of WCP regularizations -- additive and DropConnect perturbations, which impose additive noises on network weights, and make structural changes by dropping the network connections, respectively. We will show that the worse cases of both perturbations can be derived by solving respective optimization problems with spectral methods. The WCP can be minimized on both labeled and unlabeled data so that networks can be trained in a semi-supervised fashion.  This leads to a novel paradigm of semi-supervised classifiers by stabilizing the predicted outputs in presence of the worse-case perturbations imposed on the network weights and structures.

## Motivation



