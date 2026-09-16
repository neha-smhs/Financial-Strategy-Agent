"""Synthetic contract and financial safety tests; no dataset labels or IDs."""
import unittest
from datetime import date,timedelta
from decimal import Decimal
from copy import deepcopy
from common import money,day,add_months,ZERO
from forecast import Forecast,FX,CashFlow
from planner import solve,validate_plan,Plan
from evidence import Evidence,extract_ocr_amount

class NoEvidence:
    backend='local'
    def message(self,row):return Evidence.message(self,row)

def event(eid,amount,when,category='groceries',status='scheduled',direction='debit',description='Upcoming bill',linked=''):
    return dict(event_id=eid,user_id='test_user',event_type='income' if direction=='credit' else 'expense',
                description=description,category=category,direction=direction,amount=str(amount),currency='USD',
                event_date=when,settlement_date=when,status=status,linked_event_id=linked,
                flexibility='fixed',minimum_allowed_amount='')

def fixture(events=(),amount='300',balance='1000',minimum='200',methods='full_payment|partial_payment|installments',partial='true',deadline='2026-02-28',messages=(),same_day_order='debits_first'):
    r=dict(request_id='synthetic_request',user_id='test_user',request_date='2026-01-01',request_type='purchase',
           requested_amount=amount,desired_completion_date=deadline,allows_partial_payment=partial,request_text='Can I afford this?')
    p=dict(user_id='test_user',home_currency='USD',current_available_balance=balance,minimum_balance_to_keep=minimum,
           financial_priorities='housing',expense_categories_to_protect='groceries|rent',
           expense_categories_user_is_willing_to_reduce='streaming',expense_categories_user_is_willing_to_stop='streaming',
           payment_methods_user_will_consider=methods,max_installment_months='3')
    return Forecast(r,p,list(events),list(messages),[],NoEvidence(),FX([]),same_day_order=same_day_order)

def option(first='2026-01-01',freq='30',n='2',pay='150',fee='0',total='300'):
    return dict(payment_option_id='offer_a',request_id='synthetic_request',payment_method='installments',
                payment_amount=pay,number_of_payments=n,first_payment_date=first,payment_frequency_days=freq,
                financing_fee=fee,total_payable_amount=total)

class AgentContractTests(unittest.TestCase):
    def test_simple_full_payment(self):
        row,trace=solve(fixture(),[])
        self.assertEqual(row['affordability_status'],'affordable_now')
        self.assertEqual(row['amount_safe_to_pay'],'300.00')

    def test_future_expense_caps_today(self):
        f=fixture([event('bill','600','2026-02-10')]);self.assertEqual(f.capacity(),money(200))
        self.assertFalse(f.safe([(f.start,money(300))]))

    def test_pending_credit_does_not_improve_capacity(self):
        f=fixture([event('refund','99999','2026-01-05',status='pending',direction='credit',category='refund')])
        self.assertEqual(f.capacity(),fixture().capacity())

    def test_failed_debit_and_scheduled_retry(self):
        f=fixture([event('failed','600','2025-12-30',status='failed'),event('retry','600','2026-01-05',linked='failed')])
        self.assertEqual(f.capacity(),money(200));self.assertEqual(len(f.flows),1)

    def test_duplicate_pending_debit(self):
        a=event('first','600','2026-01-03',status='pending');b={**a,'event_id':'second'}
        self.assertEqual(fixture([a,b]).capacity(),fixture([a]).capacity())

    def test_linked_settlement_replaces_authorization(self):
        a=event('hold','600','2026-01-03',status='pending');b=event('posted','600','2026-01-04',status='scheduled',linked='hold')
        self.assertEqual(fixture([a,b]).capacity(),money(200))

    def test_past_spending_not_replayed(self):
        f=fixture([event('past','99999','2025-12-01',status='settled')]);self.assertEqual(f.capacity(),money(300))

    def test_unrealized_gain_unavailable(self):
        e=event('asset','1000000','2026-01-05',status='unrealized',direction='credit');e['event_type']='investment_valuation'
        self.assertEqual(fixture([e]).capacity(),fixture().capacity())

    def test_minimum_monotonicity(self):
        es=[event('bill','500','2026-02-01')]
        self.assertLessEqual(fixture(es,minimum='400').capacity(),fixture(es).capacity())

    def test_extra_expense_cannot_improve_capacity(self):
        self.assertLessEqual(fixture([event('bill','700','2026-02-01')]).capacity(),fixture().capacity())

    def test_partial_is_exactly_two_payments(self):
        es=[event('bill','600','2026-01-05'),event('pay','1000','2026-01-15',direction='credit',category='salary',description='Next confirmed salary')]
        f=fixture(es,methods='partial_payment');row,trace=solve(f,[])
        self.assertEqual(row['recommended_payment_method'],'partial_payment')
        self.assertEqual(row['payment_plan'],'2026-01-01:200.00|2026-01-15:100.00')
        self.assertEqual(row['affordability_status'],'affordable_with_plan')

    def test_partial_requires_request_permission(self):
        f=fixture(methods='partial_payment',partial='false');row,_=solve(f,[])
        self.assertEqual(row['recommended_payment_method'],'not_recommended')

    def test_installments_can_be_preferred_when_full_refused(self):
        f=fixture(methods='installments');row,_=solve(f,[option()])
        self.assertEqual(row['recommended_payment_method'],'installments')
        self.assertEqual(row['earliest_date_for_full_payment'],'2026-01-01')

    def test_late_installment_rejected(self):
        row,_=solve(fixture(methods='installments'),[option(freq='90')])
        self.assertEqual(row['recommended_payment_method'],'not_recommended')

    def test_small_first_payment_not_sufficient(self):
        f=fixture([event('bill','650','2026-01-20')],methods='installments')
        row,_=solve(f,[option()]);self.assertEqual(row['recommended_payment_method'],'not_recommended')

    def test_cheaper_wait_outranks_financing(self):
        es=[event('bill','700','2026-01-05'),event('pay','500','2026-01-15',direction='credit',category='salary',description='Next confirmed salary')]
        f=fixture(es,partial='false');row,_=solve(f,[option(first='2026-01-15',pay='165',fee='30',total='330')])
        self.assertEqual(row['recommended_payment_method'],'wait')

    def test_full_payment_date_can_exceed_deadline_without_recommendation(self):
        es=[event('bill','700','2026-01-05'),event('pay','500','2026-02-15',direction='credit',category='salary',description='Next confirmed salary')]
        row,_=solve(fixture(es,deadline='2026-01-20'),[])
        self.assertEqual(row['recommended_payment_method'],'not_recommended');self.assertEqual(row['earliest_date_for_full_payment'],'2026-02-15')

    def test_minimum_breach_before_later_payment_not_ignored(self):
        es=[event('bill','900','2026-01-05'),event('pay','1000','2026-01-15',direction='credit',category='salary',description='Next confirmed salary')]
        f=fixture(es);self.assertIsNone(f.earliest())

    def test_debit_before_same_day_credit_stress(self):
        es=[event('bill','900','2026-01-15'),event('pay','1000','2026-01-15',direction='credit',category='salary',description='Next confirmed salary')]
        self.assertFalse(fixture(es).safe())
        self.assertTrue(fixture(es,same_day_order='credits_first').safe())

    def test_final_payroll_not_recurring(self):
        es=[event('pay','500','2025-12-15',status='settled',direction='credit',category='salary',description='Final employer payroll')]
        f=fixture(es);self.assertFalse(any(x.amount>0 for x in f.flows))

    def test_cancel_does_not_delete_real_refund(self):
        e=event('refund','100','2026-01-15',direction='credit',category='refund',status='settled');e['linked_event_id']='past'
        f=fixture([event('past','100','2025-12-01',status='settled'),e]);self.assertEqual(len(f.flows),1)

    def test_month_end_and_leap_year(self):
        self.assertEqual(add_months(date(2024,1,31),1),date(2024,2,29))
        self.assertEqual(add_months(date(2024,2,29),1,31),date(2024,3,31))

    def test_fx_direction_and_date(self):
        fx=FX([dict(rate_date='2026-01-15',from_currency='EUR',to_currency='USD',rate='1.10')])
        self.assertEqual(fx.convert(money(10),'EUR','USD',day('2026-01-15')),money(11))
        with self.assertRaises(ValueError):fx.convert(money(10),'USD','EUR',day('2026-01-15'))

    def test_net_salary_not_gross(self):
        a,_,_=extract_ocr_amount('Salary: USD 900\nTotal earnings: USD 1000\nNet Pay: USD 750','Net salary')
        self.assertEqual(a,money(750))

    def test_outstanding_not_original_total(self):
        a,_,_=extract_ocr_amount('Total Amount to be Received 2400.00\nAmount Received: 600.00','Outstanding rent balance')
        self.assertEqual(a,money(1800))

    def test_ocr_unreadable_not_zero(self):
        a,_,_=extract_ocr_amount('Unclear handwriting','Outstanding bill');self.assertIsNone(a)

    def test_multilingual_salary_amendment(self):
        row={'message_id':'message','message_text':'Gaji bulanan Anda naik menjadi IDR 9000000. Perubahan berlaku mulai 2026-01-15.'}
        f=NoEvidence().message(row);self.assertEqual(f['salary_amount'],'9000000');self.assertEqual(f['salary_date'],'2026-01-15')

    def test_spending_change_does_not_inflate_reported_capacity(self):
        es=[]
        for i,when in enumerate(['2025-10-10','2025-11-10','2025-12-10']):
            e=event('subscription_'+str(i),'20',when,category='streaming',status='settled',description='Video subscription')
            e['event_type']='subscription';e['flexibility']='stoppable';es.append(e)
        f=fixture(es,amount='30',balance='260');row,trace=solve(f,[])
        self.assertEqual(row['amount_safe_to_pay'],'0.00')
        self.assertEqual(row['affordability_status'],'affordable_with_plan')
        self.assertEqual(row['spending_changes_needed'],'stop:subscription_2')

    def test_protected_expense_cannot_be_stopped(self):
        es=[]
        for i,when in enumerate(['2025-10-10','2025-11-10','2025-12-10']):
            e=event('rent_'+str(i),'20',when,category='rent',status='settled',description='Rent')
            e['flexibility']='stoppable';es.append(e)
        f=fixture(es,amount='30',balance='260');f.profile['expense_categories_user_is_willing_to_stop']='rent'
        row,_=solve(f,[]);self.assertEqual(row['recommended_payment_method'],'not_recommended')

    def test_lower_fee_offer_beats_earlier_expensive_offer(self):
        a=option(first='2026-01-01',pay='160',fee='20',total='320')
        b={**option(first='2026-01-05'),'payment_option_id':'offer_b'}
        row,t=solve(fixture(methods='installments'),[a,b]);self.assertEqual(t['chosen'].option_id,'offer_b')

    def test_schema_rejects_unmatched_installments(self):
        f=fixture();p=Plan('installments',[(f.start,money(99)),(day('2026-01-31'),money(201))],'offer_a')
        with self.assertRaises(AssertionError):validate_plan(f,p,[option()],f.capacity(),f.earliest())

if __name__=='__main__':unittest.main()
