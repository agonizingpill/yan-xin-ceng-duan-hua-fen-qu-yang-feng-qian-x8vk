from sqlalchemy import String,Float,ForeignKey,JSON,Boolean,CheckConstraint,UniqueConstraint,Text
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    email:Mapped[str]=mapped_column(String(120),unique=True)
    password:Mapped[str]=mapped_column(String(256))
    role:Mapped[str]=mapped_column(String(20))
class Audit(Base):
    __tablename__='audit'
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(120))
    action:Mapped[str]=mapped_column(Text)
    at:Mapped[str]=mapped_column(String(40))
class Borehole(Base):
    __tablename__='boreholes'
    __table_args__=(CheckConstraint('total_depth>0 AND cut_quota>0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    site:Mapped[str]=mapped_column(String(120))
    # 同工区化验样袋投票按该字段分组；留空时由孔位描述推导
    work_area:Mapped[str]=mapped_column(String(120),default='')
    total_depth:Mapped[float]=mapped_column(Float)
    cut_quota:Mapped[float]=mapped_column(Float)
    state:Mapped[str]=mapped_column(String(20),default='open')
class CoreRun(Base):
    __tablename__='core_runs'
    __table_args__=(UniqueConstraint('borehole_id','code'),CheckConstraint('start_m>=0 AND end_m>start_m AND recovered_m>=0 AND recovered_m<=end_m-start_m'))
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    code:Mapped[str]=mapped_column(String(40))
    start_m:Mapped[float]=mapped_column(Float)
    end_m:Mapped[float]=mapped_column(Float)
    recovered_m:Mapped[float]=mapped_column(Float)
class Reading(Base):
    __tablename__='readings'
    __table_args__=(UniqueConstraint('borehole_id','depth_m'),CheckConstraint('depth_m>=0 AND density>0 AND susceptibility>=0 AND velocity>0'))
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    run_id:Mapped[int]=mapped_column(ForeignKey('core_runs.id'))
    depth_m:Mapped[float]=mapped_column(Float)
    density:Mapped[float]=mapped_column(Float)
    susceptibility:Mapped[float]=mapped_column(Float)
    velocity:Mapped[float]=mapped_column(Float)
class MissingInterval(Base):
    __tablename__='missing_intervals'
    __table_args__=(CheckConstraint('start_m>=0 AND end_m>start_m'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    start_m:Mapped[float]=mapped_column(Float)
    end_m:Mapped[float]=mapped_column(Float)
    reason:Mapped[str]=mapped_column(String(600),default='')
    state:Mapped[str]=mapped_column(String(20),default='observed')
class Interpretation(Base):
    __tablename__='interpretations'
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    # gmm: 本孔物性聚类候选；lab: 同工区化验样袋物性投票候选
    kind:Mapped[str]=mapped_column(String(20),default='gmm')
    state:Mapped[str]=mapped_column(String(20),default='queued')
    evidence:Mapped[dict]=mapped_column(JSON,default=dict)
    error:Mapped[str]=mapped_column(String(600),default='')
    source_digest:Mapped[str]=mapped_column(String(64),default='')
    invalid_reason:Mapped[str]=mapped_column(String(600),default='')
    # 已确认解释的来源后来破签/撤回时保留解释与快照，只标记来源失效
    source_invalidated:Mapped[bool]=mapped_column(Boolean,default=False)
class Stratum(Base):
    __tablename__='strata'
    __table_args__=(UniqueConstraint('interpretation_id','start_m'),CheckConstraint('start_m>=0 AND end_m>start_m'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    interpretation_id:Mapped[int]=mapped_column(ForeignKey('interpretations.id'))
    start_m:Mapped[float]=mapped_column(Float)
    end_m:Mapped[float]=mapped_column(Float)
    lithology:Mapped[str]=mapped_column(String(100))
    contact_note:Mapped[str]=mapped_column(String(600))
class SampleInterval(Base):
    __tablename__='sample_intervals'
    __table_args__=(CheckConstraint('start_m>=0 AND end_m>start_m'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    stratum_id:Mapped[int]=mapped_column(ForeignKey('strata.id'))
    code:Mapped[str]=mapped_column(String(60),unique=True)
    start_m:Mapped[float]=mapped_column(Float)
    end_m:Mapped[float]=mapped_column(Float)
    purpose:Mapped[str]=mapped_column(String(200))
    destructive:Mapped[bool]=mapped_column(Boolean,default=True)
    state:Mapped[str]=mapped_column(String(20),default='planned')
class Seal(Base):
    __tablename__='seals'
    id:Mapped[int]=mapped_column(primary_key=True)
    sample_id:Mapped[int]=mapped_column(ForeignKey('sample_intervals.id'))
    code:Mapped[str]=mapped_column(String(60),unique=True)
    previous_id:Mapped[int|None]=mapped_column(ForeignKey('seals.id'),nullable=True)
    state:Mapped[str]=mapped_column(String(20),default='intact')
    reason:Mapped[str]=mapped_column(String(600),default='')
    sealed_by:Mapped[str]=mapped_column(String(120))
    snapshot:Mapped[dict]=mapped_column(JSON)
class Custody(Base):
    __tablename__='custodies'
    __table_args__=(UniqueConstraint('seal_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    seal_id:Mapped[int]=mapped_column(ForeignKey('seals.id'))
    receiver:Mapped[str]=mapped_column(String(100))
    destination:Mapped[str]=mapped_column(String(120))
    condition:Mapped[str]=mapped_column(String(300))
    at:Mapped[str]=mapped_column(String(40))
class ColumnRevision(Base):
    __tablename__='column_revisions'
    __table_args__=(UniqueConstraint('borehole_id','version'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    version:Mapped[str]=mapped_column(String(40))
    note:Mapped[str]=mapped_column(String(600))
    state:Mapped[str]=mapped_column(String(20),default='draft')
    snapshot:Mapped[dict]=mapped_column(JSON,default=dict)
    frozen_by:Mapped[str]=mapped_column(String(120),default='')


class LabRegistration(Base):
    """已签收样袋的化验岩性登记：一袋一登记，引用封签而非区间，便于破签溯源。"""
    __tablename__='lab_registrations'
    id:Mapped[int]=mapped_column(primary_key=True)
    sample_id:Mapped[int]=mapped_column(ForeignKey('sample_intervals.id'))
    seal_id:Mapped[int]=mapped_column(ForeignKey('seals.id'))
    custody_id:Mapped[int|None]=mapped_column(ForeignKey('custodies.id'),nullable=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    work_area:Mapped[str]=mapped_column(String(120),default='')
    lithology:Mapped[str]=mapped_column(String(100))
    # 袋内化验读数（多个子样/测次）与判读依据；投票时整袋只算一票
    readings:Mapped[list]=mapped_column(JSON,default=list)
    basis:Mapped[str]=mapped_column(String(600),default='')
    state:Mapped[str]=mapped_column(String(20),default='registered')
    invalid_reason:Mapped[str]=mapped_column(String(600),default='')
    registered_by:Mapped[str]=mapped_column(String(120))
    at:Mapped[str]=mapped_column(String(40))


class LabCandidate(Base):
    """化验岩性候选：对某待判测点给出岩性投票结果，确认后才成为解释。

    status: computing(计算中) / pending(待确认) / confirmed / rejected / superseded / invalidated
    """
    __tablename__='lab_candidates'
    id:Mapped[int]=mapped_column(primary_key=True)
    borehole_id:Mapped[int]=mapped_column(ForeignKey('boreholes.id'))
    interpretation_id:Mapped[int|None]=mapped_column(ForeignKey('interpretations.id'),nullable=True)
    depth_from:Mapped[float]=mapped_column(Float)
    depth_to:Mapped[float]=mapped_column(Float)
    status:Mapped[str]=mapped_column(String(20),default='computing')
    config_digest:Mapped[str]=mapped_column(String(64),default='')
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    reason:Mapped[str]=mapped_column(String(600),default='')
    decided_by:Mapped[str]=mapped_column(String(120),default='')
    decided_at:Mapped[str]=mapped_column(String(40),default='')
    at:Mapped[str]=mapped_column(String(40))


class LabCandidateRef(Base):
    """候选引用了哪些化验登记；任一登记破签/撤回即可按引用精确失效。"""
    __tablename__='lab_candidate_refs'
    __table_args__=(UniqueConstraint('candidate_id','registration_id'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    candidate_id:Mapped[int]=mapped_column(ForeignKey('lab_candidates.id'))
    registration_id:Mapped[int]=mapped_column(ForeignKey('lab_registrations.id'))


