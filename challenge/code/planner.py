"""Enumerate eligible plans, enforce the exact contract, then rank safe plans."""
from __future__ import annotations
from datetime import timedelta
from itertools import combinations, product
from dataclasses import dataclass
from common import ZERO, day, money, rounded, fmt, pipe, add_months

@dataclass
class Plan:
    method: str
    payments: list
    option_id: str = ''
    changes: tuple = ()
    minimum: object = None

def change_candidates(forecast):
    p=forecast.profile;protected=pipe(p['expense_categories_to_protect'])
    reduce_categories=pipe(p['expense_categories_user_is_willing_to_reduce'])
    stop_categories=pipe(p['expense_categories_user_is_willing_to_stop'])
    choices=[]
    for stream in forecast.streams:
        if stream.category in protected or stream.currency!=forecast.home:continue
        actions=[]
        if stream.category in stop_categories and stream.flexibility in {'stoppable','reducible_or_stoppable'}:
            actions.append({'action':'stop','event_id':stream.event_id})
        if stream.category in reduce_categories and stream.flexibility in {'reducible','reducible_or_stoppable'} and stream.minimum_allowed_amount<stream.amount:
            actions.append({'action':'reduce_to','event_id':stream.event_id,'new_amount':str(stream.minimum_allowed_amount)})
        if actions:choices.append(actions)
    for count in range(1,min(3,len(choices))+1):
        for selected in combinations(choices,count):
            yield from product(*selected)

def raw_plans(forecast, options, capacity, earliest):
    r=forecast.request;p=forecast.profile;today=forecast.start;amount=money(r['requested_amount'])
    methods=pipe(p['payment_methods_user_will_consider']);deadline=day(r['desired_completion_date'])
    if 'full_payment' in methods:
        yield Plan('full_payment',[(today,amount)])
        if earliest and today<earliest<=deadline:yield Plan('wait',[(earliest,amount)])
    if r['allows_partial_payment'].lower()=='true' and 'partial_payment' in methods and ZERO<capacity<amount and earliest and today<earliest<=deadline:
        yield Plan('partial_payment',[(today,capacity),(earliest,amount-capacity)])
    if 'installments' in methods:
        for opt in options:
            if opt['payment_method']!='installments':continue
            n=int(opt['number_of_payments']);freq=int(opt['payment_frequency_days'] or '0')
            if n<1 or (n>1 and freq<=0):continue
            first=day(opt['first_payment_date'])
            payments=[(first+timedelta(days=k*freq),money(opt['payment_amount'])) for k in range(n)]
            if payments[0][0]<today or payments[-1][0]>min(deadline,forecast.end):continue
            maximum=p['max_installment_months']
            if maximum and payments[-1][0]>add_months(first,int(maximum)):continue
            if sum((v for _,v in payments),ZERO)!=money(opt['total_payable_amount']):continue
            if money(opt['total_payable_amount'])!=amount+money(opt['financing_fee']):continue
            yield Plan('installments',payments,opt['payment_option_id'])

def validate_plan(forecast,plan,options,capacity,earliest):
    """Independent structural validation plus a direct chronological cash replay."""
    r=forecast.request;p=forecast.profile;amount=money(r['requested_amount']);deadline=day(r['desired_completion_date'])
    assert not forecast.unresolved_obligations,'Unpriced essential obligation prevents safety certification'
    assert ZERO<=capacity<=amount, 'safe amount out of bounds'
    assert plan.payments and all(v>0 for _,v in plan.payments), 'nonpositive or empty plan'
    assert plan.payments==sorted(plan.payments,key=lambda x:x[0]), 'payments out of order'
    assert all(forecast.start<=d<=min(deadline,forecast.end) for d,_ in plan.payments), 'payment outside deadline/horizon'
    methods=pipe(p['payment_methods_user_will_consider'])
    assert ('full_payment' if plan.method=='wait' else plan.method) in methods,'method refused by user'
    if plan.method=='partial_payment':
        assert r['allows_partial_payment'].lower()=='true'
        assert ZERO<capacity<amount and earliest is not None and earliest<=deadline
        assert plan.payments==[(forecast.start,capacity),(earliest,amount-capacity)],'partial-payment contract'
    if plan.method=='installments':
        option=next(o for o in options if o['payment_option_id']==plan.option_id)
        expected=[(day(option['first_payment_date'])+timedelta(days=int(option['payment_frequency_days'])*i),money(option['payment_amount'])) for i in range(int(option['number_of_payments']))]
        assert plan.payments==expected,'schedule differs from supplied offer'
        assert sum((v for _,v in plan.payments),ZERO)==money(option['total_payable_amount'])
        assert money(option['total_payable_amount'])==amount+money(option['financing_fee'])
        if p['max_installment_months']:
            assert plan.payments[-1][0]<=add_months(plan.payments[0][0],int(p['max_installment_months']))
    else:
        assert sum((v for _,v in plan.payments),ZERO)==amount,'incomplete request'
    assert len(plan.changes)<=3 and len({c['event_id'] for c in plan.changes})==len(plan.changes),'invalid changes'
    for c in plan.changes:
        stream=next(s for s in forecast.streams if s.event_id==c['event_id'])
        assert stream.category not in pipe(p['expense_categories_to_protect'])
        assert stream.currency==forecast.home
        if c['action']=='stop':
            assert stream.category in pipe(p['expense_categories_user_is_willing_to_stop'])
            assert stream.flexibility in {'stoppable','reducible_or_stoppable'}
        else:
            assert c['action']=='reduce_to'
            assert stream.category in pipe(p['expense_categories_user_is_willing_to_reduce'])
            assert stream.flexibility in {'reducible','reducible_or_stoppable'}
            assert stream.minimum_allowed_amount<=money(c['new_amount'])<stream.amount
    # This replay does not call Forecast.safe or reuse its series/minimum result.
    changes={c['event_id']:c for c in plan.changes};events=[]
    for f in forecast.flows:
        a=f.amount
        if f.recurring and f.source_id in changes and a<0:
            c=changes[f.source_id];a=ZERO if c['action']=='stop' else -money(c['new_amount'])
        priority=(0 if a<0 else 1) if forecast.same_day_order=='debits_first' else (0 if a>=0 else 1)
        events.append((f.date,priority,f.source_id,a))
    events += [(d,2,'requested_payment',-v) for d,v in plan.payments]
    balance=forecast.balance
    assert balance>=forecast.minimum,'opening buffer breached'
    for d,_,eid,a in sorted(events):
        balance+=a
        assert balance>=forecast.minimum,f'buffer breached at {d} ({eid})'

def rank(plan):
    # All candidates already finish by deadline. Additional stable tie only AFTER
    # every challenge criterion; never prioritize fewer cuts over lower fees.
    return (bool(plan.changes),sum((v for _,v in plan.payments),ZERO),
            plan.payments[0][0],len(plan.payments),plan.option_id,
            len(plan.changes),str(plan.changes))

def solve(forecast,options):
    capacity=forecast.capacity();earliest=forecast.earliest();base=list(raw_plans(forecast,options,capacity,earliest))
    eligible=[];rejected=[]
    def consider(plan):
        if forecast.unresolved_obligations:
            rejected.append({'method':plan.method,'option_id':plan.option_id,'reason':'Unpriced essential obligation'})
            return
        if not forecast.safe(plan.payments,plan.changes):
            low=forecast.minimum_balance(plan.payments,plan.changes)
            rejected.append({'method':plan.method,'option_id':plan.option_id,'reason':'minimum balance breach','date':str(low[0]),'minimum':str(low[2]),'changes':plan.changes})
            return
        try:validate_plan(forecast,plan,options,capacity,earliest)
        except AssertionError as err:
            rejected.append({'method':plan.method,'option_id':plan.option_id,'reason':str(err)});return
        plan.minimum=forecast.minimum_balance(plan.payments,plan.changes);eligible.append(plan)
    for plan in base:consider(plan)
    # Any unchanged safe plan outranks every changed plan.
    if not eligible:
        for changes in change_candidates(forecast):
            for plan in base:
                consider(Plan(plan.method,plan.payments,plan.option_id,changes))
            # A permitted reduction can also make a later payment safe sooner
            # than the unmodified earliest date. These remain with-plan outcomes.
            if 'full_payment' in pipe(forecast.profile['payment_methods_user_will_consider']):
                d=forecast.start+timedelta(days=1)
                while d<=min(forecast.end,day(forecast.request['desired_completion_date'])):
                    payment=[(d,money(forecast.request['requested_amount']))]
                    if forecast.safe(payment,changes):
                        consider(Plan('wait',payment,changes=changes));break
                    d+=timedelta(days=1)
    chosen=min(eligible,key=rank) if eligible else None
    r=forecast.request;home=forecast.home;minimum=fmt(forecast.minimum)
    row={'request_id':r['request_id'],'amount_safe_to_pay':fmt(capacity),
         'affordability_status':'not_affordable','recommended_payment_method':'not_recommended',
         'payment_plan':'none','earliest_date_for_full_payment':earliest.isoformat() if earliest else '',
         'spending_changes_needed':'none','decision_explanation':''}
    if chosen:
        status='affordable_with_plan'
        if not chosen.changes and chosen.method=='full_payment':status='affordable_now'
        elif not chosen.changes and chosen.method=='wait':status='affordable_later'
        row.update(affordability_status=status,recommended_payment_method=chosen.method,
                   payment_plan='|'.join(f'{d}:{fmt(v)}' for d,v in chosen.payments),
                   spending_changes_needed='|'.join(c['action']+':'+c['event_id']+(':'+fmt(c['new_amount']) if c['action']=='reduce_to' else '') for c in chosen.changes) or 'none')
        changes=[]
        for c in chosen.changes:
            s=next(s for s in forecast.streams if s.event_id==c['event_id'])
            changes.append(('Stop '+s.description) if c['action']=='stop' else ('Reduce '+s.description+' to '+home+' '+fmt(c['new_amount'])))
        payment_text='; '.join(f'{home} {fmt(v)} on {d}' for d,v in chosen.payments)
        row['decision_explanation']=('. '.join(changes)+'. ' if changes else '')+f'Pay {payment_text}. Forecast balance stays at or above {home} {fmt(chosen.minimum[2])}, protecting your {home} {minimum} minimum through {forecast.end}.'
    else:
        row['decision_explanation']=f'Do not proceed by {r["desired_completion_date"]}: no accepted payment arrangement completes the full request while protecting your {home} {minimum} minimum for 90 days. Safe capacity today is {home} {fmt(capacity)}.'
        if earliest:row['decision_explanation']+=f' Full-payment capacity is forecast on {earliest}, subject to your deadline and payment preferences.'
    if forecast.warnings:row['decision_explanation']+=' '+forecast.warnings[0]+'.'
    return row,{'chosen':chosen,'rejected':rejected,'eligible_count':len(eligible),'capacity':str(capacity),'earliest':str(earliest) if earliest else None}
