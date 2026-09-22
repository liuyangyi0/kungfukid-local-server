"""Native quest lists and durable accept/cancel, without invented completion.

Wire consumers:821BD0/A48FC0,821B90/A47690,821B50/A47660,
822290/A49080,822310/A48920. Server availability is explicit local policy.
Unknown completion conditions/rewards never become 'success' on client demand.
"""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import struct
from .chat import system_notice
from .maps import ClientConfig
from .menu_layouts import decode_menu_request
from .wire import Message


FAMILIES=('ordinary','daily','newbie')
ACTIONS={6050:('ordinary',2),6080:('ordinary',1),6051:('daily',2),
         6081:('daily',1),6052:('newbie',2),6082:('newbie',1)}
REQUESTS=frozenset((6000,6001,6002,6311,6312,*ACTIONS))
MAX_TASKS=256  #local bound, comfortably below the frame and client UI bounds


@dataclass(frozen=True)
class Template:
    family: str
    key: int
    fields: tuple

    @property
    def identity(self):
        #Persist the exact source row, not just an ID that can change meaning.
        #This is a small DB configuration value, not a second resource registry.
        return json.dumps(self.fields,ensure_ascii=True,separators=(',',':'))


def parse_templates(family,raw):
    if family not in FAMILIES:raise ValueError('unknown quest family')
    text=raw.decode('utf-8-sig') if raw.startswith(b'\xef\xbb\xbf') else raw.decode('gb18030')
    result={};width=41 if family=='ordinary' else 27
    for line in text.splitlines():
        if not line.strip():continue
        fields=tuple(line.split('\t'))
        if len(fields)!=width:raise ValueError('quest table width')
        key=int(fields[0])
        if not 0<key<=65535 or key in result:raise ValueError('quest duplicate/key bounds')
        if family=='daily' and not 2000<=key<=3000:raise ValueError('daily quest key range')
        if family=='newbie' and key<=3000:raise ValueError('newbie quest key range')
        if not fields[1 if family=='ordinary' else 22].strip():raise ValueError('quest name missing')
        strings={1,2,3,4} if family=='ordinary' else {2,5,8,22,23,24,25}
        words={0,36,38,39} if family=='ordinary' else {0,1,4,7,10}
        for i,value in enumerate(fields):
            if i in strings:continue
            n=int(value);bound=65535 if i in words else 0xffffffff
            if family=='ordinary' and i==40:bound=1
            if not 0<=n<=bound:raise ValueError('quest numeric bounds')
        result[key]=Template(family,key,fields)
    if not result:raise ValueError('empty quest source')
    return result


def from_config(config):
    result={}
    for family,name in zip(FAMILIES,('basequest.txt','basedailyquest.txt','basenewbiequest.txt')):
        #Missing one family must not invent templates or disable other families.
        try:rows=parse_templates(family,config.read(name))
        except (ValueError,UnicodeError):continue
        result.update({(family,key):row for key,row in rows.items()})
    return result


def configure(store,rows,templates):
    """Offline admin selects availability; not original unlock/reward policy.

    Existing progress is not reset. A changed source row cannot silently reuse
    an accepted task. An empty rule list explicitly disables this adapter.
    """
    from .ordinary_quests import validate_chains
    validate_chains(templates)
    if not isinstance(rows,list) or len(rows)>MAX_TASKS:raise ValueError('quest availability bounds')
    values=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'family','key'}:raise ValueError('quest rule fields')
        family,key=row['family'],row['key']
        if family not in FAMILIES or type(key) is not int:raise ValueError('quest rule identity')
        template=templates.get((family,key))
        if template is None or (family,key) in seen:raise ValueError('quest absent/duplicate in client source')
        if family=='ordinary' and int(template.fields[40])!=1:raise ValueError('native disabled quest')
        seen.add((family,key));values.append((family,key,template.identity))
    with store.transaction('nested quest configuration'):
        for family,key,identity in values:
            if store.quests.conflicting_progress(family,key,identity):raise ValueError('quest source conflicts with saved progress')
        store.quests.replace_availability(values)


def qualified(store,templates):
    result={}
    rows=store.quests.availability()
    if len(rows)>MAX_TASKS:raise ValueError('quest availability bounds')
    for family,key,definition in rows:
        template=templates.get((family,key))
        if template is None or template.identity!=definition:raise ValueError('quest runtime source mismatch')
        result[(family,key)]=template
    return result


def list_packets(store,uid,templates,families):
    """No rewards/progress counters are inferred from the 6000 request."""
    context=store.snapshot(uid)[2][:4];out=[]
    for family in families:
        records=[]
        for (kind,key),template in sorted(templates.items()):
            if kind!=family:continue
            saved=store.quests.progress(uid,family,key)
            state=1;baseline=bytes(116);counts=(0,0,0)
            if saved:
                definition,state,baseline=saved.definition,saved.state,saved.baseline
                counts=struct.unpack('<3I',saved.counts)
                if definition!=template.identity or state not in ((1,2,3) if family=='ordinary' else (1,2,3,4)) or len(baseline)!=116:
                    raise ValueError('quest saved state/source mismatch')
            if family=='newbie' and state==3:continue  #A480E0 erases the claimed newbie entry
            if family=='ordinary':
                records.append(context+struct.pack('<HB',key,state)+baseline)
            else:
                #The 141B record is not a reward description. Only recovered
                #identity/state/condition keys are populated; unknown bytes0.
                p=bytearray(141);struct.pack_into('<H',p,12,key);p[16]=state
                for i in range(3):struct.pack_into('<II',p,53+i*12,int(template.fields[3+i*3]),counts[i])
                records.append(bytes(p))
        ident={'ordinary':6020,'daily':6041,'newbie':6042}[family]
        body=b''.join(records)
        if family=='ordinary' and not body:body=bytes(7)
        out.append(Message(ident,body))
    return out


def transition(store,uid,template,target):
    if target not in (1,2):raise ValueError('quest transition not supported')
    with store.transaction('nested quest transition'):
        saved=store.quests.progress(uid,template.family,template.key)
        current=1 if saved is None else saved.state
        if saved and saved.definition!=template.identity:raise ValueError('quest source mismatch')
        if current not in (1,2):raise ValueError('quest terminal state cannot reset')
        if current==target:
            return False
        #A47690 copies exactly 29 profile DWORDs; cancel changes only state.
        baseline=(store.snapshot(uid)[2][129:245] if target==2 else saved.baseline)
        from .quest_rewards import frozen_rule
        execution=frozen_rule(store,template) if target==2 else None
        store.quests.transition(uid,template.family,template.key,template.identity,target,baseline,execution)
        return True


def handle(engine,c,message):
    from .engine import Phase
    from .title_rewards import announce
    if c is not engine.game or c.uid!=engine.account_uid or c.phase!=Phase.LOBBY:return []
    ident,p=message.id,message.payload
    fields=decode_menu_request(ident,p)  #shape errors are protocol errors, no mutation
    fail=lambda:[system_notice('[本地服务] 任务未开放或状态未改变，请刷新任务列表。')]
    try:templates=qualified(engine.store,getattr(engine.map_catalog,'quest_templates',{}))
    except ValueError:return fail()
    if not templates:
        engine.record_unknown(c,ident,p)
        if ident==6000:
            try:return announce(engine,c)
            except ValueError:return [system_notice('[本地服务] 奖励目录暂不可用，请检查配置。')]
        return fail()
    from .ordinary_quests import visible,complete,completion_messages
    source=getattr(engine.map_catalog,'quest_templates',{})
    enabled=templates;templates=visible(engine.store,c.uid,enabled,source)
    if ident in (6000,6001,6002):
        families=FAMILIES if ident==6000 else (FAMILIES[ident-6000],)
        try:
            fresh,rewards=complete(engine.store,c.uid,templates,getattr(engine.map_catalog,'title_levels',())) if ident==6000 else ([],[])
            after=visible(engine.store,c.uid,enabled,source)
            successors={int(source[('ordinary',key)].fields[39]) for key in fresh}
            successors={key for key in successors if ('ordinary',key) in after and ('ordinary',key) not in templates}
            templates=after
            listed={k:v for k,v in templates.items() if not (k[0]=='ordinary' and k[1] in successors)}
            out=list_packets(engine.store,c.uid,listed,families)+rewards+completion_messages(engine.store,c.uid,fresh,successors)
        except ValueError:return fail()
        #Only offered keys from this exact game connection may be acted on.
        if engine.quest_offer is None or engine.quest_offer[0] is not c:
            engine.quest_offer=(c,{})
            engine.quest_notified=set()
        offered=engine.quest_offer[1]
        for family in families:
            for old in list(offered):
                if old[0]==family:del offered[old]
            offered.update({k:v.identity for k,v in templates.items() if k[0]==family})
        from .quest_rewards import notifications
        out+=notifications(engine.store,c.uid,{k:v for k,v in templates.items() if k[0] in families},engine.quest_notified)
        if ident==6000:
            try:out+=announce(engine,c)
            except ValueError:out.append(system_notice('[本地服务] 称号奖励目录暂不可用。'))
        return out
    if ident in (6311,6312):
        from .quest_rewards import claim
        family='daily' if ident==6311 else 'newbie';key=fields['quest_id']
        template=templates.get((family,key));offer=engine.quest_offer
        if not template or not offer or offer[0] is not c or offer[1].get((family,key))!=template.identity:return fail()
        row=engine.store.quests.progress(c.uid,family,key)
        if not row or row.execution is None:
            engine.record_unknown(c,ident,p)
            return [system_notice('[本地服务] 此任务的完成条件未接通，未发放奖励。')]
        try:return claim(engine.store,c.uid,template)
        except ValueError:return [system_notice('[本地服务] 任务未完成或暂不可领取，奖励未重复发放。')]
    family,target=ACTIONS[ident];key=fields['quest_id'];template=templates.get((family,key))
    offer=engine.quest_offer
    if (template is None or not offer or offer[0] is not c or
            offer[1].get((family,key))!=template.identity):return fail()
    try:changed=transition(engine.store,c.uid,template,target)
    except ValueError:return fail()
    if family=='ordinary':
        if not changed:return fail()  #a second6060 would reset the native baseline
        return [Message(ident+10,struct.pack('<Q',c.uid)+engine.store.snapshot(c.uid)[2][:4]+struct.pack('<H',key))]
    return [Message(ident+10,struct.pack('<HB',key,target))]


def main():
    p=argparse.ArgumentParser(description='Local quest availability only; stop service and back up DB before changing rules')
    p.add_argument('--database',required=True);p.add_argument('--client-root',required=True);p.add_argument('--rules',required=True)
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing database required')
    data=json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
    if set(data)!={'schema','tasks'} or data['schema']!='kk-quest-availability-v1':raise ValueError('quest configuration shape')
    templates=from_config(ClientConfig(Path(args.client_root)/'Data/config.spf2'))
    from .store import Store
    s=Store(args.database)
    try:
        configure(s,data['tasks'],templates)
        print(json.dumps(dict(tasks=len(data['tasks']),rewards_enabled=False,policy='SYSTEM_DESIGN_INFERRED / PROVISIONAL')))
    finally:s.close()


if __name__=='__main__':main()
