"""Reproduce the submitted baseline and revised sample metrics with identical scoring."""
import argparse,importlib.util,json
from pathlib import Path
from decimal import Decimal
from common import read_csv,write_csv,INPUT_COLUMNS,OUTPUT_COLUMNS,money
from main import Agent
from evaluate import parse_plan

def score(rows,samples):
    counts={k:0 for k in ['status','method','plan','earliest','changes','amount_within_cent','amount_within_5pct_requested']}
    error=[]
    for p,s in zip(rows,samples):
        for name,key in [('status','affordability_status'),('method','recommended_payment_method'),('earliest','earliest_date_for_full_payment')]:
            counts[name]+=p[key]==s[key]
        counts['plan']+=parse_plan(p['payment_plan'])==parse_plan(s['payment_plan'])
        counts['changes']+=sorted(p['spending_changes_needed'].split('|'))==sorted(s['spending_changes_needed'].split('|'))
        e=abs(money(p['amount_safe_to_pay'])-money(s['amount_safe_to_pay']))
        counts['amount_within_cent']+=e<=Decimal('.01')
        counts['amount_within_5pct_requested']+=e<=money(s['requested_amount'])*Decimal('.05')
        error.append(e/money(s['requested_amount'])*100)
    return {'n':len(samples),'matches':counts,'mean_amount_error_pct_requested':str(sum(error)/len(error))}

def main():
    root=Path(__file__).resolve().parents[1];data=root/'dataset';out=root/'evaluation'
    spec=importlib.util.spec_from_file_location('submitted_engine',out/'baseline/submitted_engine.py')
    module=importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name]=module;spec.loader.exec_module(module);module.DATA=data
    (out/'baseline').mkdir(parents=True,exist_ok=True);(out/'public_samples').mkdir(parents=True,exist_ok=True)
    samples=read_csv(data/'sample_requests.csv');old=module.Engine();new=Agent(data,root/'.cache/evidence')
    baseline=[];revised=[]
    for s in samples:
        baseline.append(old.solve_one({k:s[k] for k in INPUT_COLUMNS}))
        revised.append(new.predict(s)[0])
    write_csv(out/'baseline/sample_predictions.csv',baseline,OUTPUT_COLUMNS)
    write_csv(out/'public_samples/sample_predictions.csv',revised,OUTPUT_COLUMNS)
    result={'baseline':score(baseline,samples),'revised':score(revised,samples),
            'scope':'25 public development examples, not an untouched holdout or hidden test. API backend not run.'}
    (out/'comparison.json').write_text(json.dumps(result,indent=2))
    lines=['# Revision evaluation', '',result['scope'],'','Both versions rerun and scored with identical numeric payment-plan normalization. Baseline source is the submitted GitHub engine at e075481. Revised default is local OCR plus rules, all recurring expenses, 90-day horizon, debits before credits. No other repository code or cached model results were copied.','',
           '| Metric | Submitted | Revised |','|---|---:|---:|']
    for k in result['baseline']['matches']:
        a=result['baseline']['matches'][k];b=result['revised']['matches'][k]
        lines.append(f'| {k} | {a}/25 ({a*4}%) | {b}/25 ({b*4}%) |')
    lines+=['',f"Mean absolute safe-amount error / requested amount: {Decimal(result['baseline']['mean_amount_error_pct_requested']):.2f}% -> {Decimal(result['revised']['mean_amount_error_pct_requested']):.2f}%.",
            '', 'Safety validation independently replays the reconstructed cash-flow events, but shares the evidence and recurrence assumptions. It cannot establish real-world safety or correctness of all extracted facts. Unknown historical image amounts are disclosed and excluded from recurrence; unknown future obligations block approval.',
            '', 'Limitations: no live API credential was available. Model transport/schema tests use mocks; no claim of measured LLM accuracy or cost is made. OCR totals need review on ambiguous layouts. Local messages use bounded patterns, not general language understanding. No hidden-test labels were accessed. Changes were not tuned to another project’s reported scores.']
    (out/'comparison.md').write_text('\n'.join(lines)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
