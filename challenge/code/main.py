#!/usr/bin/env python3
"""Run: python code/main.py --dataset dataset --output output.csv"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from collections import defaultdict,Counter
from dataclasses import asdict,is_dataclass
from datetime import date,datetime,timezone
from decimal import Decimal
from pathlib import Path
from common import INPUT_COLUMNS,OUTPUT_COLUMNS,read_csv,write_csv,money,day
from evidence import Evidence
from forecast import FX,Forecast
from planner import solve

def json_default(value):
    if is_dataclass(value):return asdict(value)
    if isinstance(value,(Decimal,date)):return str(value)
    raise TypeError(type(value).__name__)

class Agent:
    def __init__(self,dataset,cache,backend='local',model=None,variable_quantile='0.5',lookback_days=90,same_day_order='debits_first'):
        self.dataset=Path(dataset)
        self.profiles={r['user_id']:r for r in read_csv(self.dataset/'financial_profiles.csv')}
        self.events=defaultdict(list);self.messages=defaultdict(list);self.images=defaultdict(list);self.options=defaultdict(list)
        for filename,target,key in [('financial_events.csv',self.events,'user_id'),('messages.csv',self.messages,'user_id'),('images.csv',self.images,'user_id'),('request_payment_options.csv',self.options,'request_id')]:
            for row in read_csv(self.dataset/filename):target[row[key]].append(row)
        self.fx=FX(read_csv(self.dataset/'exchange_rates.csv'))
        self.evidence=Evidence(self.dataset,cache,backend,model)
        self.variable_quantile=variable_quantile;self.lookback_days=lookback_days
        self.same_day_order=same_day_order

    def predict(self,request):
        # Explicit projection makes it impossible for expected output columns to
        # leak into any downstream model prompt or solver calculation.
        request={k:request[k] for k in INPUT_COLUMNS}
        uid=request['user_id'];rid=request['request_id']
        if uid not in self.profiles:raise ValueError('No profile for '+uid)
        if money(request['requested_amount'])<=0:raise ValueError('Request amount must be positive')
        if day(request['desired_completion_date'])<day(request['request_date']):raise ValueError('Deadline precedes request')
        messages=[r for r in self.messages[uid] if not r['request_id'] or r['request_id']==rid]
        images=[r for r in self.images[uid] if not r['request_id'] or r['request_id']==rid]
        f=Forecast(request,self.profiles[uid],self.events[uid],messages,images,self.evidence,self.fx,self.variable_quantile,self.lookback_days,self.same_day_order)
        output,trace=solve(f,self.options[rid])
        trace.update(request_id=rid,profile=self.profiles[uid],facts=f.facts,streams=f.streams,flows=f.flows,
                     baseline=f.series(),warnings=f.warnings,exclusions=f.exclusions,
                     chosen_balance=f.series(trace['chosen'].payments,trace['chosen'].changes) if trace['chosen'] else [])
        return output,trace

def usage_report(agent,output,request_count,elapsed,directory):
    records=agent.evidence.records;calls=agent.evidence.usage
    inputs=sum(u.get('prompt_tokens',0) for u in calls);outputs=sum(u.get('completion_tokens',0) for u in calls)
    image_calls=sum(2 for r in records if not r['cache_hit']) if agent.evidence.backend=='local' else 0
    input_price=os.getenv('BUY_OR_WAIT_INPUT_USD_PER_MILLION');output_price=os.getenv('BUY_OR_WAIT_OUTPUT_USD_PER_MILLION')
    cost=(Decimal(inputs)*Decimal(input_price)+Decimal(outputs)*Decimal(output_price))/1000000 if calls and input_price and output_price else (Decimal(0) if not calls else None)
    try:tess=subprocess.run(['tesseract','--version'],capture_output=True,text=True).stdout.splitlines()[0]
    except (OSError,IndexError):tess='not installed'
    lines=['# Final run token usage and cost','',f'- Generated at: {datetime.now(timezone.utc).isoformat()}',
           f'- Output SHA-256: {hashlib.sha256(Path(output).read_bytes()).hexdigest()}',f'- Requests: {request_count}',f'- Runtime: {elapsed:.2f} seconds',f'- Backend: {agent.evidence.backend}',
           '', '| Provider / model | Calls | Input tokens | Output tokens | Estimated API cost |',
           '|---|---:|---:|---:|---:|']
    if agent.evidence.backend=='local':
        lines.append(f'| Local / {tess}, LSTM OEM 1 | {image_calls} OCR passes | N/A | N/A | $0 |')
        lines.append('| Local bilingual financial-fact rules | No generative calls | 0 | 0 | $0 |')
    models=defaultdict(list)
    for u in calls:models[(u['provider'],u['model'])].append(u)
    for (provider,model),us in models.items():
        it=sum(u.get('prompt_tokens',0) for u in us);ot=sum(u.get('completion_tokens',0) for u in us)
        mc=(Decimal(it)*Decimal(input_price)+Decimal(ot)*Decimal(output_price))/1000000 if input_price and output_price else None
        lines.append(f'| {provider} / {model} | {len(us)} | {it} | {ot} | {"$"+str(mc) if mc is not None else "Price inputs not configured"} |')
    lines += ['',f'- Total LLM calls: {len(calls)}',f'- Total input / output tokens: {inputs} / {outputs}',
              f'- Total tokens: {inputs+outputs}',f'- Average tokens per request: {(inputs+outputs)/request_count:.2f}',
              f'- Total estimated API cost: {"$"+str(cost) if cost is not None else "Unavailable; configure price inputs"}',
              f'- Average estimated API cost per request: {"$"+str(cost/request_count) if cost is not None else "Unavailable"}',
              f'- Images referenced: {len(records)}; image cache hits: {sum(r["cache_hit"] for r in records)}',
              '', 'Tesseract is a local neural OCR model and does not report LLM tokens. API cost excludes local compute and development-assistant usage. Development chat tokens are not exposed to this application and are not represented as measured runtime usage. A cold-cache run is recommended for the final report. Cached extraction creation costs belong to the earlier run and are not silently included as new calls.']
    (directory/'usage_report.md').write_text('\n'.join(lines)+'\n')
    (directory/'model_usage.json').write_text(json.dumps(calls,indent=2))

def run(args):
    started=time.monotonic();directory=Path(args.evaluation_dir);directory.mkdir(parents=True,exist_ok=True)
    requests=read_csv(Path(args.dataset)/args.requests)
    if len({r['request_id'] for r in requests})!=len(requests):raise ValueError('Duplicate request IDs')
    agent=Agent(args.dataset,args.cache,args.backend,args.model,args.variable_quantile,args.lookback_days,args.same_day_order)
    rows=[];failures=[];trace_dir=directory/'traces';trace_dir.mkdir(exist_ok=True)
    for i,request in enumerate(requests):
        try:
            row,trace=agent.predict(request);rows.append(row)
            (trace_dir/(request['request_id']+'.json')).write_text(json.dumps(trace,default=json_default,ensure_ascii=False,indent=2))
        except Exception as err:
            failures.append({'request_id':request['request_id'],'error':str(err),'type':type(err).__name__})
        if (i+1)%25==0:print(f'Processed {i+1}/{len(requests)}; unresolved failures: {len(failures)}',flush=True)
    (directory/'errors.json').write_text(json.dumps(failures,indent=2))
    (directory/'extracted_images.json').write_text(json.dumps(agent.evidence.records,ensure_ascii=False,indent=2))
    if failures:raise RuntimeError(f'{len(failures)} requests failed; no partial submission written. See {directory}/errors.json')
    write_csv(args.output,rows,OUTPUT_COLUMNS)
    assert len(read_csv(args.output))==len(requests)
    usage_report(agent,args.output,len(requests),time.monotonic()-started,directory)
    if args.requests=='requests.csv':
        required_report=Path(args.output).resolve().parent/'evaluation'/'usage_report.md'
        required_report.parent.mkdir(parents=True,exist_ok=True)
        required_report.write_text((directory/'usage_report.md').read_text())
    manifest={'output_sha256':hashlib.sha256(Path(args.output).read_bytes()).hexdigest(),'rows':len(rows),
              'status_counts':dict(Counter(r['affordability_status'] for r in rows)),'backend':args.backend,
              'config':{'variable_quantile':args.variable_quantile,'lookback_days':args.lookback_days,'horizon_days':90,'same_day_order':args.same_day_order},
              'python':platform.python_version(),
              'code_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))},
              'input_hashes':{str(p.relative_to(args.dataset)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(args.dataset).rglob('*')) if p.is_file() and p.suffix in {'.csv','.png'}}}
    (directory/'run_manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k:v for k,v in manifest.items() if k!='input_hashes'},indent=2))

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',default='dataset');p.add_argument('--output',default='output.csv')
    p.add_argument('--requests',default='requests.csv');p.add_argument('--evaluation-dir',default='evaluation/final')
    p.add_argument('--cache',default='.cache/evidence');p.add_argument('--backend',choices=['local','api'],default='local')
    p.add_argument('--model');p.add_argument('--variable-quantile',default='0.5');p.add_argument('--lookback-days',type=int,default=90)
    p.add_argument('--same-day-order',choices=['debits_first','credits_first'],default='debits_first')
    return p

if __name__=='__main__':run(parser().parse_args())
