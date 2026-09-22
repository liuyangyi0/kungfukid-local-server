"""Same-install SkillProperty admission facts, not a second resource registry.

A71F00 reads LogicEffects/AttackerUstate into the vector consumed by82B8D0.
This bounded in-memory view only qualifies receipts with no attacker effects;
it does not recreate the damage formula or permit cross-target status writes.
"""
from pathlib import Path
from dataclasses import dataclass
from types import MappingProxyType
from .maps import ClientConfig


def _signature(node):
    return (node.tag,tuple(sorted(node.attrib.items())),(node.text or '').strip(),
            tuple(_signature(child) for child in node))

@dataclass(frozen=True)
class UStateEffect:
    """A71F00's ordered12-byte {UstateID,Level,Cycle}; no seconds conversion."""
    code:int
    level:int
    cycle:int

def _effect(node):
    values=tuple(int(node.attrib.get(name,'0'),10) for name in ('UstateID','Level','Cycle'))
    if any(not -0x80000000<=v<=0x7fffffff for v in values):raise ValueError('UState triple integer width')
    return UStateEffect(*values)


class CombatCatalog:
    def __init__(self, safe_receipt_ids, conflicts=(), *, source=None, guard_break_ids=(),attacker_effects=None,target_effects=None,invalid_effect_ids=()):
        self.safe_receipt_ids=frozenset(safe_receipt_ids)
        self.conflicts=frozenset(conflicts)
        self.source=source
        self.guard_break_ids=frozenset(guard_break_ids)-self.conflicts
        self.invalid_effect_ids=frozenset(invalid_effect_ids)
        self.attacker_effects=MappingProxyType({k:tuple(v) for k,v in (attacker_effects or {}).items() if k not in self.conflicts and k not in self.invalid_effect_ids})
        self.target_effects=MappingProxyType({k:tuple(v) for k,v in (target_effects or {}).items() if k not in self.conflicts and k not in self.invalid_effect_ids})

    @classmethod
    def from_xml(cls,root,*,source=None):
        if root.tag!='SkillProperty' or len(root)>100000:raise ValueError('skill table shape')
        rows={};conflicts=set();safe=set();guard_break=set();attacker={};target={};invalid=set()
        for node in root:
            if node.tag!='PropertyItem':raise ValueError('skill table unexpected row')
            try:ident=int(node.attrib['SkillProId'])
            except (KeyError,ValueError):raise ValueError('invalid skill identity') from None
            if not 0<ident<=0x7fffffff:raise ValueError('skill identity range')
            signature=_signature(node)
            if ident in rows and rows[ident]!=signature:
                conflicts.add(ident);safe.discard(ident)
            rows.setdefault(ident,signature)
            # A71F00 stores DefenceTear at SkillProperty+38h. Missing or
            # malformed data never qualifies an additional receipt outcome.
            try:tear=int(node.attrib.get('DefenceTear','0'))
            except ValueError:tear=0
            if -0x80000000<=tear<=0x7fffffff and tear!=0:guard_break.add(ident)
            # Unknown child/attribute semantics must not turn into permission.
            shape_ok=all(child.tag in ('HitEffect','LogicEffects') for child in node)
            effect_lists=node.findall('LogicEffects')
            shape_ok=shape_ok and len(effect_lists)<=1 and all(
                child.tag in ('TargetUstate','AttackerUstate')
                for effects in effect_lists for child in effects)
            try:
                if not shape_ok:raise ValueError('unknown effect structure')
                attacker[ident]=tuple(_effect(child) for child in node.findall('./LogicEffects/AttackerUstate'))
                target[ident]=tuple(_effect(child) for child in node.findall('./LogicEffects/TargetUstate'))
            except ValueError:invalid.add(ident);safe.discard(ident)
            if shape_ok and ident not in conflicts and ident not in invalid and not attacker.get(ident):
                safe.add(ident)
        return cls(safe,conflicts,source=source,guard_break_ids=guard_break,attacker_effects=attacker,target_effects=target,invalid_effect_ids=invalid)

    @classmethod
    def from_client(cls,root):
        config=ClientConfig(Path(root)/'Data/config.spf2')
        return cls.from_xml(config.xml('skillproperty.xml'),source=str(config.path))

    @classmethod
    def from_file(cls,path):
        """Explicit server-owned source XML, not client attestation or upload.

        Reuse the same semantic projection as local installations. Do not load
        DLLs/scripts or synthesize skill definitions when content is missing.
        """
        import xml.etree.ElementTree as ET
        source=Path(path)
        if not 1<=source.stat().st_size<=16*1024*1024:raise ValueError('skill XML size')
        raw=source.read_bytes()
        if b'\0' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('skill XML declarations forbidden')
        root=ET.fromstring(raw)
        return cls.from_xml(root,source=str(source))

    def permits_effect_free_receipt(self,skill_id):
        return self.receipt_policy(skill_id)=='effect_free'

    def receipt_policy(self,skill_id):
        if skill_id in self.conflicts or skill_id in self.invalid_effect_ids:return 'invalid_definition'
        if self.attacker_effects.get(skill_id):return 'attacker_effect_correlation_required'
        if skill_id in self.safe_receipt_ids:return 'effect_free'
        return 'definition_missing'

    def receipt_outcomes(self,skill_id,hit_status,callback_flag):
        #979870 emits8121/status2 BEFORE testing guard break; only afterward
        #does it emit8126/status4. The target's active-property gate remains
        #native: this permits its report, it does not calculate a guard break.
        if (self.permits_effect_free_receipt(skill_id) and skill_id in self.guard_break_ids
                and hit_status==2 and callback_flag==1):
            return (2,4)
        return (hit_status,)
