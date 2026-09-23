"""Single feature-switch and content preparation path for offline/lab modes."""
from dataclasses import dataclass
from .configuration import match_point_policy


@dataclass(frozen=True)
class GameContent:
    maps: object
    combat: object
    talismans: object


def validate_features(args,*,shared):
    rates=match_point_policy(args)
    observers=getattr(args,'experimental_mode1_spectators',0)
    rounds=getattr(args,'experimental_team_series_rounds',0)
    if type(observers) is not int or not 0<=observers<=8:raise ValueError('spectator capacity must be0..8')
    if type(rounds) is not int or rounds not in (0,1,3,5,7):raise ValueError('invalid team series rounds')
    modes=[getattr(args,key,False) for key in ('experimental_stage21','experimental_mode10','experimental_tutorial')]
    if any(type(value) is not bool for value in modes):raise ValueError('feature switches must be boolean')
    if (rates or observers or rounds or any(modes)) and not shared:
        raise ValueError('selected room features require shared multi-account rooms')
    if any(modes) and not getattr(args,'map_client_root',None):
        raise ValueError('selected mode requires installed client data')


def load_content(args,*,shared):
    validate_features(args,shared=shared)
    from ..maps import MapCatalog
    from ..combat_catalog import CombatCatalog
    from ..talisman import TalismanCatalog
    root=getattr(args,'map_client_root',None)
    maps=MapCatalog.from_client(root) if root else None
    combat=CombatCatalog.from_client(root) if root else None
    talismans=TalismanCatalog.from_client(root) if root and shared else None
    if getattr(args,'experimental_stage21',False):
        from ..stage_catalog import from_client
        maps.stage_plans=from_client(root)
    if getattr(args,'experimental_mode10',False):
        from ..foster_catalog import from_client
        maps.foster_plans=from_client(root)
    if getattr(args,'experimental_tutorial',False):
        if 2 not in maps.title_levels:raise ValueError('tutorial requires qualified title table')
        maps.tutorial_enabled=True
    return GameContent(maps,combat,talismans)


def room_hub(args,content):
    from ..rooms import RoomHub
    return RoomHub(lab_no_award_settlement=True,match_point_rewards=match_point_policy(args),
        combat_catalog=content.combat,talisman_catalog=content.talismans,network_probe=True,
        spectator_capacity=getattr(args,'experimental_mode1_spectators',0),
        team_series_rounds=getattr(args,'experimental_team_series_rounds',0))
