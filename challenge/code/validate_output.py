"""Reconstruct inputs and independently validate a serialized submission."""
import argparse,json
from pathlib import Path
from common import read_csv,OUTPUT_COLUMNS,money,day
from main import Agent
from forecast import Forecast
from planner import Plan,validate_plan

def validate(args):
    a=Agent(args.dataset,args.cache,backend=args.backend,model=args.model,variable_quantile=args.variable_quantile,same_day_order=args.same_day_order)
    reqs={r['request_id']:r for r in read_csv(Path(args.dataset)/'requests.csv')}
    rows=read_csv(args.output)
    assert rows and list(rows[0])==OUTPUT_COLUMNS,'Wrong schema or column order'
    assert len(rows)==len(reqs) and {r['request_id'] for r in rows}==set(reqs),'Missing or duplicate requests'
    counts={'rows':len(rows),'recommended_plans_verified':0,'not_recommended':0,'unpriced_obligations':0};failures=[]
    for row in rows:
        try:
            r=reqs[row['request_id']];uid=r['user_id'];rid=r['request_id'];p=a.profiles[uid]
            ms=[m for m in a.messages[uid] if not m['request_id'] or m['request_id']==rid]
            ims=[m for m in a.images[uid] if not m['request_id'] or m['request_id']==rid]
            f=Forecast(r,p,a.events[uid],ms,ims,a.evidence,a.fx,args.variable_quantile,90,args.same_day_order)
            capacity=money(row['amount_safe_to_pay']);earliest=day(row['earliest_date_for_full_payment']) if row['earliest_date_for_full_payment'] else None
            assert capacity==f.capacity(),'Wrong unmodified safe capacity'
            assert earliest==f.earliest(),'Wrong unmodified full-payment date'
            assert row['affordability_status'] in {'affordable_now','affordable_with_plan','affordable_later','not_affordable'}
            method=row['recommended_payment_method']
            if method=='not_recommended':
                assert row['affordability_status']=='not_affordable' and row['payment_plan']=='none' and row['spending_changes_needed']=='none'
                counts['not_recommended']+=1;counts['unpriced_obligations']+=bool(f.unresolved_obligations);continue
            assert method in {'full_payment','partial_payment','installments','wait'}
            payments=[(day(x.split(':')[0]),money(x.split(':')[1])) for x in row['payment_plan'].split('|')]
            changes=[]
            if row['spending_changes_needed']!='none':
                for value in row['spending_changes_needed'].split('|'):
                    parts=value.split(':');c={'action':parts[0],'event_id':parts[1]}
                    if parts[0]=='reduce_to':assert len(parts)==3;c['new_amount']=parts[2]
                    else:assert len(parts)==2
                    changes.append(c)
            option_id=''
            if method=='installments':
                from datetime import timedelta
                matches=[]
                for o in a.options[rid]:
                    if o['payment_method']!='installments':continue
                    schedule=[(day(o['first_payment_date'])+timedelta(days=int(o['payment_frequency_days'])*i),money(o['payment_amount'])) for i in range(int(o['number_of_payments']))]
                    if payments==schedule:matches.append(o['payment_option_id'])
                assert matches,'No matching installment offer';option_id=sorted(matches)[0]
            plan=Plan(method,payments,option_id,tuple(changes))
            validate_plan(f,plan,a.options[rid],capacity,earliest)
            if row['affordability_status']=='affordable_now':
                assert method=='full_payment' and not changes and earliest==f.start
            elif row['affordability_status']=='affordable_later':assert method=='wait' and not changes
            else:assert row['affordability_status']=='affordable_with_plan' and (changes or method in {'partial_payment','installments'})
            counts['recommended_plans_verified']+=1
        except Exception as error:failures.append({'request_id':row['request_id'],'error':str(error)})
    result={**counts,'failures':failures,'scope':'Safety relative to reconstructed forecast; does not claim hidden ground-truth accuracy.'}
    Path(args.report).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if failures:raise SystemExit(1)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',default='dataset');p.add_argument('--output',default='output.csv')
    p.add_argument('--cache',default='.cache/evidence');p.add_argument('--report',default='evaluation/output_validation.json')
    p.add_argument('--backend',choices=['local','api'],default='local');p.add_argument('--model')
    p.add_argument('--variable-quantile',default='0.5');p.add_argument('--same-day-order',default='debits_first')
    validate(p.parse_args())
