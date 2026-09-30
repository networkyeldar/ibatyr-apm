import argparse
from artifacts import acquire
p=argparse.ArgumentParser();p.add_argument('component',choices=['server','agent','all']);a=p.parse_args()
for c in ['server','agent'] if a.component=='all' else [a.component]:print(acquire(c))
