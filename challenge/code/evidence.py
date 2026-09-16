"""Bounded evidence extraction. Financial decisions never come from this module.

Local mode uses Tesseract LSTM OCR and bilingual financial-fact rules. Optional
OpenAI-compatible mode uses constrained JSON extraction with source quotations.
All caches are content-addressed and contain evidence, never request answers.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from common import money, fmt

from api_evidence import APIExtractor, PROMPT, validate_facts
VERSION = 'evidence-v3'

INJECTION = re.compile(r'ignore (?:all |any |the )?(?:previous|prior|above) instructions|(?:you must|assistant must|always) (?:approve|output|override)|pay the (?:release|processing) charge|bayar biaya (?:pencairan|pemrosesan)', re.I)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def parse_number(token):
    token = token.strip().strip('.,')
    if ',' in token and '.' not in token:
        parts = token.split(',')
        token = ''.join(parts[:-1])+'.'+parts[-1] if len(parts[-1]) == 2 else ''.join(parts)
    elif ',' in token:
        token = token.replace(',', '')
    return money(token)

def numbers(text):
    return [parse_number(m.group()) for m in re.finditer(r'(?<![\w])\d[\d,]*(?:\.\d{1,2})?(?!\w)',text)]

def words_amount(text):
    """Read amounts written in words, useful when currency symbols confuse OCR."""
    units = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(),range(20)))
    units.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10))))
    scales={'hundred':100,'thousand':1000,'million':1000000,'lakh':100000,'crore':10000000}
    def parse(s):
        acc=0;group=0;seen=False
        for w in re.findall('[a-z]+',s.lower()):
            if w in units:group+=units[w];seen=True
            elif w=='hundred':group=max(group,1)*100
            elif w in scales:acc+=max(group,1)*scales[w];group=0
        return acc+group if seen else None
    # Some invoices put currency BEFORE the amount and wrap the paise phrase
    # around tax columns. Numeric tax values are not interpreted as number words.
    prefix=re.search(r'Indian\s+Rupees?\s+([\s\S]{0,300}?)\s+Paise',text,re.I)
    if prefix:
        phrase=prefix.group(1)
        parts=re.split(r'\band\b',phrase,flags=re.I)
        if len(parts)>=2:
            major=parse(' and '.join(parts[:-1]));minor=parse(parts[-1])
            if major is not None and minor is not None and minor<100:
                return money(major)+money(minor)/100
    # Currency words separate major and minor units. Reject line-item prose.
    m=re.search(r'((?:(?:[A-Za-z]+)[ -]+){2,30})(?:rupees?|rupiahs?)(.*)',text,re.I)
    if m:
        major=parse(m.group(1));minor=parse(m.group(2)) if re.search('pais[ae]',m.group(2),re.I) else 0
        if major is not None:return money(major)+money(minor or 0)/100
    return None

def extract_ocr_amount(text, description):
    """Rank semantic total labels, not filenames, IDs or expected answers."""
    desc=description.lower(); lines=text.splitlines(); candidates=[]
    salary=bool(re.search(r'salary|payroll|net pay',desc))
    outstanding=bool(re.search(r'outstanding|payable|due',desc))
    amount_due=None;received=None;gross=None
    for i,line in enumerate(lines):
        low=line.lower()
        # Handwritten thousands separators can be recognized as a hyphen.
        numeric_line=re.sub(r'(?<=\d)-(?=\d{3}(?:\D|$))','',line) if 'total' in low else line
        vals=numbers(numeric_line)
        if not vals:continue
        value=vals[-1];score=0
        if salary:
            if re.search(r'net\s*pay|take.home|gaji bersih',low):score=120
        else:
            if re.search(r'grand\s*total|total\s*paid|total\s*amount\s*received',low):score=110
            elif re.search(r'amount\s*payable|balance\s*due',low):score=105 if outstanding else 90
            elif re.search(r'\btotal\b',low) and not re.search(r'sub\s*total|subtotal|total.*items|total.*earnings|total.*deduction',low):score=95
            elif re.search(r'net\s*amount|cash\s*paid|item\s*bill',low):score=70
            if re.search(r'amount\s*received|amount\s*paid',low):received=value
            if re.search(r'total\s*amount\s*to\s*be',low):gross=value
            if re.search(r'amount\s*due\s*till',low):amount_due=value
            if 'amount due after' in low:score=0
        if score:candidates.append((score,value,line))
    if outstanding and gross is not None and received is not None:
        return max(money(0),gross-received),'outstanding total minus received',candidates
    if not salary:
        word_value=words_amount(text)
        if word_value is not None and word_value>0:
            candidates.append((115,word_value,'Amount written in words'))
    if not candidates:return None,'No readable total label',[]
    candidates.sort(key=lambda x:x[0],reverse=True)
    return candidates[0][1],candidates[0][2],candidates

class Evidence:
    def __init__(self, dataset, cache, backend='local', model=None):
        self.dataset=Path(dataset);self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.backend=backend;self.model=model or os.getenv('BUY_OR_WAIT_MODEL','')
        self.records=[];self.usage=[]
        self.api=APIExtractor(self.model,self.usage)

    def remote(self, text, image_path=None):
        return self.api.extract(text, image_path)

    def image(self, row, event):
        path=self.dataset/'media'/'images'/(row['image_id']+'.png')
        if not path.exists():raise FileNotFoundError(f'Missing image: {path}')
        signature=digest(path.read_bytes()+event['description'].encode()+self.backend.encode()+self.model.encode()+(VERSION+PROMPT+os.getenv('OPENAI_BASE_URL','')).encode())
        target=self.cache/(signature+'.json')
        cached=target.exists()
        if cached:
            result=json.loads(target.read_text())
            if self.backend=='api': validate_facts({k:v for k,v in result.items() if k not in {'ocr_text','engine'}})
        elif self.backend=='api':
            result=self.remote('Extract the amount for this event: '+json.dumps(event),path)
            result['ocr_text']='';result['engine']=self.model
        else:
            env={**os.environ,'OMP_THREAD_LIMIT':'1'}
            texts=[]
            for psm in [6,11]:
                p=subprocess.run(['tesseract',str(path),'stdout','--oem','1','--psm',str(psm)],capture_output=True,text=True,env=env,timeout=45,check=True)
                texts.append(p.stdout)
            options=[]
            for t in texts:
                amount,quote,candidates=extract_ocr_amount(t,event['description'])
                if amount is not None:options.append((max(c[0] for c in candidates) if candidates else 130,amount,quote))
            options.sort(key=lambda x:x[0],reverse=True)
            result={'amount':str(options[0][1]) if options else None,'currency':event['currency'],
                    'evidence_quote':options[0][2] if options else 'Unresolved OCR',
                    'ocr_text':'\n'.join(texts),'engine':'Tesseract LSTM OEM 1',
                    'candidates':[{'score':s,'amount':str(a),'quote':q} for s,a,q in options]}
        if not cached:target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.records.append({'image_id':row['image_id'],'event_id':event['event_id'],'sha256':digest(path.read_bytes()),'cache_hit':cached,**result})
        return result

    def message(self, row):
        text=row['message_text'];low=text.lower()
        if INJECTION.search(text):
            return {'evidence_quote':text,'salary_ended':False,'ignored_embedded_instruction':True}
        if self.backend=='api':
            signature=digest((VERSION+PROMPT+self.model+os.getenv('OPENAI_BASE_URL','')+text).encode());target=self.cache/(signature+'.json')
            if target.exists():result=validate_facts(json.loads(target.read_text()),text)
            else:result=self.remote(text);target.write_text(json.dumps(result))
            return result
        facts={'evidence_quote':text,'salary_ended':False}
        amounts=[(m.group(1),str(parse_number(m.group(2)))) for m in re.finditer(r'\b(INR|ZAR|IDR|USD|EUR)\s+([\d,]+(?:\.\d{1,2})?)',text)]
        dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',text)
        # Ended employment is distinct from one household income ending.
        if re.search(r'your employment has ended|kontrak musiman.*berakhir|seasonal contract has ended|pekerjaan anda.*berakhir',low):facts['salary_ended']=True
        if re.search(r'salary|monthly pay|gaji|regular pay',low) and amounts:
            facts['salary_currency'],facts['salary_amount']=amounts[0]
            if dates:facts['salary_date']=dates[0]
            facts['salary_confirmed']=bool(re.search(r'confirm|dikonfirmasi|scheduled|dijadwalkan|next salary|first salary|gaji pertama|temporary monthly|gaji bulanan|salary.*increased|salary.*resumes',low))
        if re.search(r'confirmed salary is now expected|gaji.*(?:diharapkan|diperkirakan).*\d{4}-',low) and dates:
            facts['salary_date']=dates[0];facts['salary_delay']=True
        if re.search(r'one household|salah satu sumber pendapatan',low):facts['replace_household_income']=True
        if re.search(r'approved an invoice|menyetujui pembayaran faktur',low) and amounts and dates:
            facts['one_time_currency'],facts['one_time_income']=amounts[0];facts['one_time_date']=dates[0]
        if re.search(r'one.time arrears|tunggakan satu kali',low) and len(amounts)>1:
            facts['arrears_currency'],facts['arrears_amount']=amounts[1]
        rent=re.search(r'(?:rent|sewa).*?(\d+(?:\.\d+)?)\s*%',low)
        if rent:facts['rent_increase_percent']=rent.group(1)
        if re.search(r'childcare|penitipan anak',low):facts['unpriced_childcare']=True
        if facts.get('salary_amount') and not facts.get('salary_confirmed'):
            facts.pop('salary_amount',None);facts.pop('salary_currency',None)
        facts['source_id']=row['message_id']
        return facts
