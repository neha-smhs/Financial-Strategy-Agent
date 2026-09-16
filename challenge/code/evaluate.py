"""Evaluate public examples without exposing labels to inference."""
import argparse,json
from pathlib import Path
from collections import defaultdict
from decimal import Decimal
from common import read_csv,write_csv,OUTPUT_COLUMNS,money
from main import Agent,json_default

def parse_plan(value):
    if value=='none':return []
    return [(x.split(':')[0],money(x.split(':')[1])) for x in value.split('|')]

def evaluate(args):
    dataset=Path(args.dataset);dest=Path(args.report_dir);dest.mkdir(parents=True,exist_ok=True)
    samples=read_csv(dataset/'sample_requests.csv')
    agent=Agent(dataset,args.cache,args.backend,args.model,args.variable_quantile,args.lookback_days,args.same_day_order)
    profiles=agent.profiles;predictions=[];details=[];metrics=defaultdict(int);errors=defaultdict(list);warnings=[]
    for sample in samples:
        prediction,trace=agent.predict(sample);predictions.append(prediction)
        currency=profiles[sample['user_id']]['home_currency']
        ae=abs(money(prediction['amount_safe_to_pay'])-money(sample['amount_safe_to_pay']))
        errors[currency].append(ae)
        result={'request_id':sample['request_id'],'currency':currency,'absolute_amount_error':str(ae)}
        for key in ['affordability_status','recommended_payment_method','earliest_date_for_full_payment']:
            match=sample[key]==prediction[key];metrics[key]+=int(match)
            result[key]={'expected':sample[key],'actual':prediction[key],'match':match}
        match=parse_plan(sample['payment_plan'])==parse_plan(prediction['payment_plan']);metrics['payment_plan']+=int(match)
        result['payment_plan']={'expected':sample['payment_plan'],'actual':prediction['payment_plan'],'match':match}
        metrics['amount_within_cent']+=int(ae<=Decimal('.01'))
        result['spending_changes_needed']={'expected':sample['spending_changes_needed'],'actual':prediction['spending_changes_needed']}
        metrics['spending_changes_exact']+=int(sample['spending_changes_needed']==prediction['spending_changes_needed'])
        result['warnings']=trace['warnings'];details.append(result)
    summary={'n':len(samples),'matches':dict(metrics),'rates':{k:v/len(samples) for k,v in metrics.items()},
             'mae_by_currency':{k:str(sum(v)/len(v)) for k,v in errors.items()},
             'config':{'quantile':args.variable_quantile,'lookback_days':args.lookback_days,'same_day_order':args.same_day_order},
             'scope':'All 25 public examples; development evaluation, not a hidden-test or holdout result.'}
    (dest/'metrics.json').write_text(json.dumps(summary,indent=2));(dest/'sample_comparison.json').write_text(json.dumps(details,indent=2))
    write_csv(dest/'sample_predictions.csv',predictions,OUTPUT_COLUMNS)
    lines=['# Public sample evaluation','',summary['scope'],'','| Field | Matches | Accuracy |','|---|---:|---:|']
    for k,v in metrics.items():lines.append(f'| {k} | {v}/{len(samples)} | {v/len(samples):.1%} |')
    lines+=['','| Currency | Safe-amount MAE |','|---|---:|']+[f'| {k} | {v} |' for k,v in summary['mae_by_currency'].items()]
    lines+=['','The deterministic validator checks feasibility against this implementation\'s reconstructed forecast. It cannot prove that inferred spending exactly matches the organizer\'s hidden forecast. Sample errors are retained in sample_comparison.json. No sample labels are inputs to Agent.predict.']
    (dest/'report.md').write_text('\n'.join(lines)+'\n');print(json.dumps(summary,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',default='dataset');p.add_argument('--report-dir',default='evaluation/public_samples')
    p.add_argument('--cache',default='.cache/evidence');p.add_argument('--backend',default='local');p.add_argument('--model')
    p.add_argument('--variable-quantile',default='0.5');p.add_argument('--lookback-days',type=int,default=90)
    p.add_argument('--same-day-order',choices=['debits_first','credits_first'],default='debits_first')
    evaluate(p.parse_args())
