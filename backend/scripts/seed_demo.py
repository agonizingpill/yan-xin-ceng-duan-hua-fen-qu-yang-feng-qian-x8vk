import json
import numpy as np
from sqlalchemy import select
from src.database import Session,Base,engine
from src.models import User,Borehole,CoreRun,Reading,MissingInterval,Interpretation,Stratum,SampleInterval,Seal,Custody,ColumnRevision,LabRegistration,LabCandidate,LabCandidateRef
from src.security import password_hash

def seed():
    Base.metadata.create_all(engine)
    with Session.begin()as s:
        for role in ['operator','supervisor','viewer']:
            if not s.scalar(select(User).where(User.email==role+'@liheng.local')):s.add(User(email=role+'@liheng.local',password=password_hash('Core2026!'),role=role))
        h=s.scalar(select(Borehole).where(Borehole.code=='BH-31'))
        if h and not h.work_area:h.work_area='砾衡青石沟东线'
        if not h:
            h=Borehole(code='BH-31',site='砾衡青石沟东线 / ZK-31',total_depth=36,cut_quota=6);s.add(h);s.flush()
            rng=np.random.default_rng(42)
            for code,start,end,recovered in [('R01',0,18,17),('R02',18,36,18)]:
                run=CoreRun(borehole_id=h.id,code=code,start_m=start,end_m=end,recovered_m=recovered);s.add(run);s.flush()
                for depth in np.arange(start+.5,end,1):
                    if depth==12.5:continue
                    group=min(int(depth//12),2);center=[(2.1,12,2200),(2.6,50,4100),(3.1,100,6100)][group]
                    s.add(Reading(borehole_id=h.id,run_id=run.id,depth_m=float(depth),density=round(float(center[0]+rng.normal(0,.035)),4),susceptibility=round(float(center[1]+rng.normal(0,1.5)),4),velocity=round(float(center[2]+rng.normal(0,55)),3)))
            s.add(MissingInterval(borehole_id=h.id,start_m=12,end_m=13,state='observed',reason=''))
            legacy=Interpretation(borehole_id=h.id,state='historical',evidence={'note':'旧纸本区间来源，仅保留既有切样，不替代新GMM解释'});s.add(legacy);s.flush();st=Stratum(interpretation_id=legacy.id,start_m=0,end_m=12,lithology='旧编录砂岩',contact_note='纸本编录回录，仅保留历史取样依据');s.add(st);s.flush();sample=SampleInterval(borehole_id=h.id,stratum_id=st.id,code='CS-031-OLD',start_m=8,end_m=9,purpose='历史抗压试验',destructive=True,state='broken');s.add(sample);s.flush();s.add(Seal(sample_id=sample.id,code='CX031-001',state='broken',reason='运输交接发现铅封裂口，待现场核样重封',sealed_by='operator@liheng.local',snapshot={'code':sample.code,'start_m':8,'end_m':9,'source':'历史纸本'}))
        # 同工区化验样板：BH-31 已交接样袋，作为新孔化验岩性研判的投票来源。
        if not s.scalar(select(Borehole).where(Borehole.code=='BH-30')):
            h30=Borehole(code='BH-30',site='砾衡青石沟东线 / ZK-30',work_area='砾衡青石沟东线',total_depth=12,cut_quota=3);s.add(h30);s.flush()
            run=CoreRun(borehole_id=h30.id,code='R01',start_m=0,end_m=12,recovered_m=12);s.add(run);s.flush()
            rng=np.random.default_rng(7)
            for depth in np.arange(.5,12,1):
                s.add(Reading(borehole_id=h30.id,run_id=run.id,depth_m=float(depth),density=round(float(2.05+rng.normal(0,.03)),4),susceptibility=round(float(11+rng.normal(0,1.2)),4),velocity=round(float(2150+rng.normal(0,45)),3)))
            i30=Interpretation(borehole_id=h30.id,kind='gmm',state='confirmed',evidence={'note':'既有人工层段，已取样交接'});s.add(i30);s.flush()
            st30=Stratum(interpretation_id=i30.id,start_m=0,end_m=12,lithology='粉砂岩',contact_note='岩芯颜色粒径与回次接触面人工判读');s.add(st30);s.flush()
            area='砾衡青石沟东线'
            # 同种岩性的两袋样板：袋内读数再多，投票时整袋只算一票。
            for code,start,end,extra in [('CS-030-A',1,2,(2.08,12,2180)),('CS-030-B',4,5,(2.02,10,2140))]:
                sample=SampleInterval(borehole_id=h30.id,stratum_id=st30.id,code=code,start_m=start,end_m=end,purpose='化验岩性比对',destructive=False,state='transferred');s.add(sample);s.flush()
                seal=Seal(sample_id=sample.id,code='CX030-'+code[-1],state='intact',reason='',sealed_by='operator@liheng.local',snapshot={'code':sample.code});s.add(seal);s.flush()
                cust=Custody(seal_id=seal.id,receiver='周岩 / 实验室收样员',destination='砾衡岩土试验室',condition='双方核对样号与铅封完整，样袋干燥无漏失',at='2026-11-18T09:00:00+00:00');s.add(cust);s.flush()
                readings=[{'density':round(extra[0]+rng.normal(0,.02),4),'susceptibility':round(extra[1]+rng.normal(0,.8),4),'velocity':round(extra[2]+rng.normal(0,30),3)} for _ in range(4)]
                s.add(LabRegistration(sample_id=sample.id,seal_id=seal.id,custody_id=cust.id,borehole_id=h30.id,work_area=area,lithology='粉砂岩',readings=readings,basis='薄片与粒度分析，四个测次结果一致，判为粉砂岩',state='registered',registered_by='operator@liheng.local',at='2026-11-18T11:00:00+00:00'))
            # 一袋已登记但破签的样板：确认失效链路和原证据留存。
            sample=SampleInterval(borehole_id=h30.id,stratum_id=st30.id,code='CS-030-C',start_m=8,end_m=9,purpose='化验岩性比对',destructive=False,state='broken');s.add(sample);s.flush()
            seal=Seal(sample_id=sample.id,code='CX030-C',state='broken',reason='现场复核发现铅封断裂，暂停引用',sealed_by='operator@liheng.local',snapshot={'code':sample.code});s.add(seal);s.flush()
            s.add(LabRegistration(sample_id=sample.id,seal_id=seal.id,custody_id=None,borehole_id=h30.id,work_area=area,lithology='泥质砂岩',readings=[{'density':2.6,'susceptibility':50,'velocity':4100}],basis='原登记判为泥质砂岩，后破签作废，仅留档',state='invalidated',invalid_reason='封签 CX030-C 已破签：现场复核发现铅封断裂，暂停引用',registered_by='operator@liheng.local',at='2026-11-18T12:00:00+00:00'))
    with Session()as s:return {cls.__tablename__:len(s.scalars(select(cls)).all())for cls in [User,Borehole,CoreRun,Reading,MissingInterval,Interpretation,Stratum,SampleInterval,Seal,Custody,ColumnRevision,LabRegistration,LabCandidate,LabCandidateRef]}
if __name__=='__main__':print(json.dumps(seed(),ensure_ascii=False))
