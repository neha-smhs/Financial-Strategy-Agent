"""Reconstruct obligations and forecast a fixed 90-day request-relative window."""
from __future__ import annotations
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from datetime import timedelta
from decimal import Decimal
from statistics import median
import re
from common import ZERO, day, money, rounded, add_months, quantile, pipe

@dataclass(frozen=True)
class CashFlow:
    date: object
    amount: Decimal             # positive credit, negative debit
    source_id: str
    description: str
    category: str
    recurring: bool = False

@dataclass
class Stream:
    event_id: str
    category: str
    description: str
    amount: Decimal
    currency: str
    first: object
    frequency_days: int        # zero means calendar-monthly
    anchor_day: int
    flexibility: str
    minimum_allowed_amount: Decimal
    observations: int

class FX:
    def __init__(self, rows):
        self.rates={(r['rate_date'],r['from_currency'],r['to_currency']):money(r['rate']) for r in rows}
    def convert(self, amount, currency, home, when):
        if currency==home:return rounded(amount)
        key=(when.isoformat(),currency,home)
        if key not in self.rates:raise ValueError(f'Missing supplied dated FX rate {key}')
        return rounded(amount*self.rates[key])

class Forecast:
    def __init__(self, request, profile, events, messages, images, evidence, fx,
                 variable_quantile='0.5', lookback_days=90, same_day_order='debits_first'):
        self.request=request;self.profile=profile;self.start=day(request['request_date'])
        self.end=self.start+timedelta(days=90);self.home=profile['home_currency']
        self.balance=money(profile['current_available_balance']);self.minimum=money(profile['minimum_balance_to_keep'])
        self.fx=fx;self.streams=[];self.flows=[];self.warnings=[];self.exclusions=[];self.facts=[];self.unresolved_obligations=[]
        self.q=variable_quantile;self.lookback=lookback_days
        if same_day_order not in {'debits_first','credits_first'}:raise ValueError('Unknown same-day ordering')
        self.same_day_order=same_day_order
        events=[dict(e) for e in events]
        self.event_map={e['event_id']:e for e in events}
        self.image_event_ids={i['related_event_id'] for i in images}
        for img in images:
            event=self.event_map.get(img['related_event_id'])
            if not event:raise ValueError('Image links to an unknown event')
            result=evidence.image(img,event)
            if not event['amount']:
                if result.get('amount') is None:
                    # A past one-off record is not an unknown future cash obligation.
                    # Preserve missingness and expose it; never silently assign zero.
                    if event['settlement_date'] and day(event['settlement_date'])<self.start and event['status']=='settled':
                        self.warnings.append(f"Unresolved historical image amount for {event['event_id']}; excluded from recurrence, already reflected in current balance")
                    else:raise ValueError('Unresolved future image amount: '+event['event_id'])
                else:event['amount']=str(money(result['amount']))
        for row in sorted(messages,key=lambda x:x['sent_at']):
            if row['sent_at'][:10]>request['request_date']:
                self.exclusions.append({'source_id':row['message_id'],'reason':'Evidence after request date'});continue
            fact=evidence.message(row);fact['source_id']=row['message_id'];self.facts.append(fact)
            eid=row.get('related_event_id')
            if eid and eid in self.event_map:
                text=row['message_text'].lower();ev=self.event_map[eid]
                if fact.get('ignored_embedded_instruction'): continue
                if fact.get('event_status'): ev['status']=fact['event_status']
                if re.search(r'(?:payment|bill|charge|debit) (?:has been |was |is )cancelled',text): ev['status']='cancelled'
                if re.search(r'payment was received|was paid|payment has settled|pembayaran.*diterima',text):ev['status']='settled'
                # A failed attempt can still have a separate scheduled retry.
                if 'previous debit attempt failed' in text:ev['status']='failed'
        self.events=events
        self._build()

    def _add(self, date_, amount, source_id, description, category, recurring=False):
        if self.start<=date_<=self.end:self.flows.append(CashFlow(date_,rounded(amount),source_id,description,category,recurring))

    def _build(self):
        valid=[];seen=set();superseded=set()
        for e in self.events:
            linked=self.event_map.get(e['linked_event_id'])
            if linked and e['status'] in {'settled','scheduled'} and linked['status'] in {'pending','cancelled','failed'} and linked['direction']==e['direction']:
                superseded.add(linked['event_id'])
        for e in self.events:
            status=e['status'];eid=e['event_id']
            if eid in superseded or status in {'cancelled','failed','unrealized'} or e['event_type']=='investment_valuation':
                self.exclusions.append({'source_id':eid,'reason':'Superseded, failed, cancelled, or non-cash'});continue
            fingerprint=tuple(e[k] for k in ['user_id','event_type','description','category','direction','amount','currency','event_date','settlement_date','status'])
            if fingerprint in seen:
                self.exclusions.append({'source_id':eid,'reason':'Duplicate financial record'});continue
            seen.add(fingerprint)
            if not e['amount']:
                if status in {'pending','scheduled'} and e['direction']=='debit':
                    self.unresolved_obligations.append('Unpriced obligation '+eid)
                    self.warnings.append('Unpriced obligation '+eid+' prevents safety certification')
                continue
            if status=='pending' and e['direction']=='credit':
                self.exclusions.append({'source_id':eid,'reason':'Unsettled credit is unavailable'});continue
            valid.append(e)
        # Scheduled salary is handled with its recurring stream below.
        for e in valid:
            if not e['settlement_date']:continue
            d=day(e['settlement_date'])
            if e['status'] in {'pending','scheduled'} and e['direction']=='debit':
                when=max(self.start,d)
                self._add(when,-self.fx.convert(money(e['amount']),e['currency'],self.home,d),e['event_id'],e['description'],e['category'])
            elif e['status']=='settled' and d>self.start:
                self._add(d,self.fx.convert(money(e['amount']),e['currency'],self.home,d)*(1 if e['direction']=='credit' else -1),e['event_id'],e['description'],e['category'])
            elif e['status']=='scheduled' and e['direction']=='credit' and e['category']!='salary' and not re.search(r'bonus|commission|refund|prize|lottery|investment|windfall', e['category']+' '+e['description'], re.I):
                self._add(d,self.fx.convert(money(e['amount']),e['currency'],self.home,d),e['event_id'],e['description'],e['category'])
        history=[e for e in valid if e['status']=='settled' and e['settlement_date'] and day(e['settlement_date'])<=self.start]
        groups=defaultdict(list)
        for e in history:
            if e['direction']!='debit' or e['event_type'] not in {'expense','subscription','debt_payment','investment_purchase'}:continue
            if e['event_id'] in self.image_event_ids or e['linked_event_id']:continue
            if re.search(r'authorization|reversed|disputed|one.time|internal transfer|card purchase|purchase later|pending',e['description'],re.I):continue
            groups[(e['category'],e['currency'])].append(e)
        for (category,currency),rows in groups.items():
            rows.sort(key=lambda e:e['settlement_date'])
            if len(rows)<3:continue
            dates=sorted(set(day(e['settlement_date']) for e in rows))
            gaps=[(b-a).days for a,b in zip(dates,dates[1:])]
            if len(gaps)<2:continue
            gap=int(median(gaps));latest=rows[-1];last=dates[-1]
            if gap<=0 or gap>62:continue
            monthly=(26<=gap<=32 and max(Counter(d.day for d in dates).values())>=len(dates)*.6)
            if (self.start-last).days>max(gap*2,62):
                self.warnings.append('Stale expense recurrence excluded: '+latest['event_id']);continue
            recent=[e for e in rows if (self.start-day(e['settlement_date'])).days<=self.lookback] or rows[-3:]
            amounts=[money(e['amount']) for e in recent]
            fixed=len(set(money(e['amount']) for e in rows))==1
            amount=amounts[-1] if fixed else rounded(quantile(amounts,self.q))
            # Monthly rent amendments override historical fixed amounts.
            for fact in self.facts:
                if category in {'rent','housing'} and fact.get('rent_increase_percent'):
                    amount=rounded(amount*(1+money(fact['rent_increase_percent'])/100))
            if monthly:
                anchor=Counter(d.day for d in dates).most_common(1)[0][0]
                first=add_months(last,1,anchor)
                while first<self.start:first=add_months(first,1,anchor)
                freq=0
            else:
                anchor=last.day;first=last+timedelta(days=gap);freq=gap
                while first<self.start:first+=timedelta(days=gap)
            stream=Stream(latest['event_id'],category,latest['description'],amount,currency,first,freq,anchor,
                          latest['flexibility'],money(latest['minimum_allowed_amount'] or '0'),len(rows))
            self.streams.append(stream)
            d=first
            while d<=self.end:
                # Explicit scheduled occurrence replaces the inferred same-category occurrence.
                explicit=any(f.date==d and f.category==category and not f.recurring and f.amount<0 for f in self.flows)
                if not explicit:self._add(d,-self.fx.convert(amount,currency,self.home,d),stream.event_id,stream.description,category,True)
                d=add_months(d,1,anchor) if monthly else d+timedelta(days=gap)
        self._income(valid,history)
        self.flows.sort(key=lambda f:(f.date, (f.amount>0 if self.same_day_order=='debits_first' else f.amount<0), f.source_id))

    def _income(self, valid, history):
        salary=[e for e in history if e['category']=='salary' and e['direction']=='credit'
                and e['event_id'] not in self.image_event_ids and not e['linked_event_id']]
        uncertain=r'bonus|commission|arrears|reimbursement|prize|project|invoice|contract|milestone|independent work|platform|marketplace|app earnings|temporary assignment|peak.season|retainer'
        regular=[e for e in salary if not re.search(uncertain,e['description'],re.I)]
        scheduled=[e for e in valid if e['category']=='salary' and e['direction']=='credit' and e['status']=='scheduled']
        amount=None;currency=self.home;date_=None;source='';ended=False
        if regular:
            regular.sort(key=lambda e:e['settlement_date']);latest=regular[-1]
            if re.search(r'final\s+(?:employer\s+)?payroll|final\s+salary',latest['description'],re.I):
                ended=True
            amount=money(latest['amount']);currency=latest['currency'];source=latest['event_id']
            latest_month=latest['settlement_date'][:7]
            active=[e for e in regular if e['settlement_date'][:7]==latest_month]
            if any('household' in e['description'].lower() for e in active):
                if len(set(e['currency'] for e in active))==1:amount=sum((money(e['amount']) for e in active),ZERO)
            date_=add_months(day(latest['settlement_date']),1)
            # Do not extrapolate vanished income across long gaps without confirmation.
            if (self.start-day(latest['settlement_date'])).days>62:amount=None
            while date_<self.start:date_=add_months(date_,1)
        for e in sorted(scheduled,key=lambda e:e['settlement_date']):
            if day(e['settlement_date'])>=self.start:
                amount=money(e['amount']);currency=e['currency'];date_=day(e['settlement_date']);source=e['event_id'];ended=False;break
        for fact in self.facts:
            if fact.get('salary_ended'):ended=True
            if fact.get('salary_amount') is not None and fact.get('salary_confirmed'):
                amount=money(fact['salary_amount']);currency=fact.get('salary_currency') or self.home;source=fact['source_id'];ended=False
                if date_ is None:
                    # Existing salary history supplies cadence; otherwise an explicit date is required.
                    prior_dates=[day(e['settlement_date']) for e in salary if e['settlement_date']]
                    if prior_dates:
                        anchor=Counter(d.day for d in prior_dates).most_common(1)[0][0]
                        date_=self.start.replace(day=min(anchor,28))
                        if date_<self.start:date_=add_months(date_,1,anchor)
            if fact.get('salary_date'):date_=day(fact['salary_date'])
            if fact.get('one_time_income') is not None:
                when=fact.get('one_time_date') or fact.get('salary_date')
                if when:self._add(day(when),self.fx.convert(money(fact['one_time_income']),fact.get('one_time_currency') or self.home,self.home,day(when)),fact['source_id'],'Confirmed one-time income','salary')
            if fact.get('unpriced_childcare'):
                if not any(re.search(r'childcare|child care|penitipan anak',s.description,re.I) for s in self.streams):
                    reason='New recurring childcare is confirmed but its amount is missing; affordability cannot be certified'
                    self.warnings.append(reason);self.unresolved_obligations.append(reason)
        if ended:
            self.exclusions.append({'source_id':source,'reason':'Employment ended; no recurring income forecast'});return
        if amount is None or date_ is None:return
        if date_<self.start:
            # An obsolete first-pay date is not evidence of a new windfall today.
            while date_<self.start:date_=add_months(date_,1)
        anchor=date_.day;first=date_
        while date_<=self.end:
            self._add(date_,self.fx.convert(amount,currency,self.home,date_),source,'Confirmed / supported recurring salary','salary',True)
            date_=add_months(date_,1,anchor)
        for fact in self.facts:
            if fact.get('arrears_amount'):
                self._add(first,self.fx.convert(money(fact['arrears_amount']),fact.get('arrears_currency') or currency,self.home,first),fact['source_id'],'Confirmed one-time payroll arrears','salary')

    def series(self, payments=(), changes=()):
        """Closing balance per day, debits before credits; payment after posted flows.

        We also check every intermediate debit, preventing same-day income netting
        from hiding a bill shortfall. The opening balance is always checked.
        """
        change_map={c['event_id']:c for c in changes};pay=defaultdict(lambda:ZERO)
        for date_,amount in payments:pay[date_]+=amount
        perday=defaultdict(list)
        for flow in self.flows:perday[flow.date].append(flow)
        bal=self.balance;result=[(self.start,'opening',bal)]
        d=self.start
        while d<=self.end:
            for f in perday[d]:
                amount=f.amount
                if f.recurring and f.source_id in change_map and amount<0:
                    c=change_map[f.source_id]
                    amount=ZERO if c['action']=='stop' else -money(c['new_amount'])
                bal+=amount;result.append((d,f.source_id,rounded(bal)))
            if pay[d]:bal-=pay[d];result.append((d,'requested_payment',rounded(bal)))
            result.append((d,'close',rounded(bal)));d+=timedelta(days=1)
        return result

    def minimum_balance(self, payments=(), changes=()):
        return min(self.series(payments,changes),key=lambda x:x[2])

    def safe(self, payments=(), changes=()):
        if any(d<self.start or d>self.end or a<=0 for d,a in payments):return False
        return not self.unresolved_obligations and self.minimum_balance(payments,changes)[2]>=self.minimum

    def capacity(self):
        if self.unresolved_obligations:return ZERO
        request_amount=money(self.request['requested_amount'])
        available=min(self.balance-self.minimum,self.minimum_balance()[2]-self.minimum)
        return rounded(max(ZERO,min(request_amount,available)))

    def earliest(self):
        if self.unresolved_obligations:return None
        amount=money(self.request['requested_amount']);d=self.start
        while d<=self.end:
            if self.safe([(d,amount)]):return d
            d+=timedelta(days=1)
        return None
