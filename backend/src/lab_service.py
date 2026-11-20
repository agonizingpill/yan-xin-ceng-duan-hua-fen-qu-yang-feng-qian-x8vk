"""化验岩性登记与同工区样袋物性投票。

规则：
- 只有已签收交接、当前封签完好的样袋才能登记化验岩性；
- 新孔用同工区有效化验样袋的物性辨认岩性，同一袋内读数再多也只投一票；
- 几种岩性票数接近、无法区分时该测点留待判，不生成岩性；
- 破签或撤回化验后，引用该登记的所有候选立即失效并拒绝确认；
- 已确认解释保留快照与引用明细，只标记来源失效，仍可查原证据。
"""
import hashlib
import json
from math import exp

from fastapi import HTTPException
from sqlalchemy import delete, select

from .common import audit, get, now, pack
from .models import (
    Borehole, Custody, Interpretation, LabCandidate, LabCandidateRef,
    LabRegistration, Reading, SampleInterval, Seal, Stratum,
)

# 物性量纲差异很大，先按野外仪器的典型起伏归一化再求相似度。
SCALES = {'density': 0.5, 'susceptibility': 30.0, 'velocity': 1500.0}
# 只有足够接近的样袋才参与投票，也才构成候选的“引用”关系。
MIN_SIMILARITY = 0.25
TOP_SHARE = 0.6
MIN_MARGIN = 0.15


def work_area(borehole):
    return (borehole.work_area or borehole.site.split('/')[0]).strip()


def digest(registrations):
    payload = [
        {'id': r.id, 'lithology': r.lithology, 'readings': r.readings}
        for r in sorted(registrations, key=lambda z: z.id)
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


def _seal_valid(s, sample, seal):
    if not seal or seal.state != 'intact' or seal.sample_id != sample.id:
        return False
    latest = s.scalar(
        select(Seal.id).where(Seal.sample_id == sample.id).order_by(Seal.id.desc())
    )
    if latest != seal.id:
        return False
    return s.scalar(select(Custody.id).where(Custody.seal_id == seal.id)) is not None


def registration_valid(s, reg):
    if not reg or reg.state != 'registered':
        return False
    sample = s.get(SampleInterval, reg.sample_id)
    if not sample or sample.state != 'transferred':
        return False
    seal = s.get(Seal, reg.seal_id)
    return _seal_valid(s, sample, seal)


def valid_registrations(s, area, lock=False):
    rows = s.scalars(
        select(LabRegistration)
        .where(
            LabRegistration.state == 'registered', LabRegistration.work_area == area
        )
        .order_by(LabRegistration.id)
    ).all()
    if lock:
        # 按 id 顺序锁定登记行，和失效传播共用顺序，避免与破签/撤回交叉等待。
        rows = s.scalars(
            select(LabRegistration)
            .where(LabRegistration.id.in_([r.id for r in rows] or [0]))
            .order_by(LabRegistration.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    return [r for r in rows if registration_valid(s, r)]


def register(s, p, u):
    ref = get(s, SampleInterval, p.sample_id)
    from .common import hole

    h = hole(s, ref.borehole_id)
    sample = get(s, SampleInterval, p.sample_id, True)
    if sample.state != 'transferred':
        raise HTTPException(409, '仅已签收交接的样袋可登记化验岩性')
    seal = s.scalar(
        select(Seal).where(Seal.sample_id == sample.id).order_by(Seal.id.desc())
    )
    if not _seal_valid(s, sample, seal):
        raise HTTPException(409, '样袋当前封签不完整或尚未签收交接，不能登记化验岩性')
    custody = s.scalar(select(Custody).where(Custody.seal_id == seal.id))
    if s.scalar(
        select(LabRegistration.id).where(
            LabRegistration.sample_id == sample.id,
            LabRegistration.state == 'registered',
        )
    ):
        raise HTTPException(409, '该样袋已有有效化验登记，撤回后才能重新登记')
    x = LabRegistration(
        sample_id=sample.id,
        seal_id=seal.id,
        custody_id=custody.id,
        borehole_id=sample.borehole_id,
        work_area=work_area(h),
        lithology=p.lithology,
        readings=[r.model_dump() for r in p.readings],
        basis=p.basis,
        state='registered',
        registered_by=u['email'],
        at=now(),
    )
    s.add(x)
    s.flush()
    audit(s, u, '登记化验岩性 ' + sample.code + ' / ' + p.lithology)
    return pack(x)


def start(s, p, u):
    """开立化验岩性解释，并为每个实测点建立计算占位候选与引用快照。"""
    from .common import hole

    h = hole(s, p.borehole_id)
    if s.scalar(
        select(Interpretation.id).where(
            Interpretation.borehole_id == h.id,
            Interpretation.kind == 'gmm',
            Interpretation.state.in_(['queued', 'confirmed']),
        )
    ):
        raise HTTPException(409, '本孔已有排队或已确认的物性层段解释，不能重复研判')
    if s.scalar(
        select(Interpretation.id).where(
            Interpretation.borehole_id == h.id,
            Interpretation.state.in_(['queued', 'computing', 'ready']),
        )
    ):
        raise HTTPException(409, '本孔已有待确认解释，请先处理或撤销')
    points = s.scalars(
        select(Reading).where(Reading.borehole_id == h.id).order_by(Reading.depth_m)
    ).all()
    if not points:
        raise HTTPException(409, '本孔尚无可用于比对的实测物性点')
    area = work_area(h)
    regs = valid_registrations(s, area, lock=True)
    if not regs:
        raise HTTPException(409, '同工区暂无可引用的有效化验样袋')
    dg = digest(regs)
    x = Interpretation(
        borehole_id=h.id,
        kind='lab',
        state='queued',
        source_digest=dg,
        evidence={
            'kind': 'lab',
            'work_area': area,
            'source_digest': dg,
            'pool_registration_ids': [r.id for r in regs],
        },
    )
    s.add(x)
    s.flush()
    for point in points:
        c = LabCandidate(
            borehole_id=h.id,
            interpretation_id=x.id,
            depth_from=point.depth_m,
            depth_to=point.depth_m,
            status='computing',
            config_digest=dg,
            result={'depth_m': point.depth_m},
            at=now(),
        )
        s.add(c)
        s.flush()
        # 先引用整批候选池，使并发破签在计算回包前就能找到并锁住本孔候选。
        for r in regs:
            s.add(LabCandidateRef(candidate_id=c.id, registration_id=r.id))
    audit(s, u, '申请同工区化验样袋岩性研判')
    return pack(x)


def _value(obj, key):
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def _bag_similarity(target, readings):
    best = 0.0
    for r in readings or []:
        try:
            d2 = sum(((_value(target, f) - float(r[f])) / SCALES[f]) ** 2 for f in SCALES)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        best = max(best, exp(-0.5 * d2))
    return best


def _classify(point, regs, s):
    votes = {}
    detail = {}
    for reg in regs:
        similarity = _bag_similarity(point, reg.readings)
        if similarity < MIN_SIMILARITY:
            continue
        sample = s.get(SampleInterval, reg.sample_id)
        seal = s.get(Seal, reg.seal_id)
        votes[reg.lithology] = votes.get(reg.lithology, 0.0) + similarity
        detail.setdefault(reg.lithology, []).append({
            'registration_id': reg.id,
            'sample_code': sample.code if sample else '',
            'seal_code': seal.code if seal else '',
            'similarity': round(similarity, 6),
        })
    total = sum(votes.values())
    if total <= 0:
        return None, {
            'depth_m': point.depth_m,
            'lithology': None,
            'decided': False,
            'reason': '同工区样板物性与该测点差异过大，无可用选票，留待判',
            'votes': [],
            'referenced_registration_ids': [],
        }
    ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
    tally = [
        {
            'lithology': name,
            'weight': round(weight, 6),
            'share': round(weight / total, 6),
            'bags': sorted(detail[name], key=lambda b: b['registration_id']),
        }
        for name, weight in ranked
    ]
    top_name, top_weight = ranked[0]
    top_share = top_weight / total
    second_share = ranked[1][1] / total if len(ranked) > 1 else 0.0
    if top_share < TOP_SHARE or top_share - second_share < MIN_MARGIN:
        tied = '、'.join(
            '%s %.0f%%' % (name, weight / total * 100) for name, weight in ranked[:3]
        )
        return None, {
            'depth_m': point.depth_m,
            'lithology': None,
            'decided': False,
            'reason': '多种岩性样本物性相近，无法区分（%s），留待判' % tied,
            'votes': tally,
            'referenced_registration_ids': [
                b['registration_id'] for v in tally for b in v['bags']
            ],
        }
    return top_name, {
        'depth_m': point.depth_m,
        'lithology': top_name,
        'decided': True,
        'reason': '',
        'votes': tally,
        'referenced_registration_ids': [
            b['registration_id'] for v in tally for b in v['bags']
        ],
    }


def compute(s, x, h):
    """worker 在持孔锁的事务内调用；迟到的计算不得复活已失效候选。"""
    evidence = x.evidence or {}
    area = evidence.get('work_area') or work_area(h)
    pool_ids = list(evidence.get('pool_registration_ids') or [])
    # 先锁候选行：迟到的计算一旦发现来源失效，就把候选钉死在 invalidated。
    candidates = s.scalars(
        select(LabCandidate)
        .where(LabCandidate.interpretation_id == x.id)
        .order_by(LabCandidate.depth_from, LabCandidate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if any(c.status == 'invalidated' for c in candidates):
        _kill(s, x, '引用样袋已破签或化验已撤回，候选已失效，不再重算')
        return
    if x.state == 'failed':
        return
    # 再锁来源登记，顺序与破签/撤回一致（候选 → 登记），避免交叉等待。
    valid = {r.id: r for r in valid_registrations(s, area, lock=True)}
    if any(i not in valid for i in pool_ids):
        _kill(s, x, '引用样袋已破签、撤回或交接失效，候选不再可用，须重新研判')
        return
    if x.state != 'queued':
        return
    if digest([valid[i] for i in pool_ids]) != x.source_digest:
        _kill(s, x, '引用样袋化验登记已变更，候选来源与快照不一致，须重新研判')
        return
    regs = [valid[i] for i in pool_ids]
    points = {
        r.depth_m: r
        for r in s.scalars(select(Reading).where(Reading.borehole_id == h.id))
    }
    for c in candidates:
        if c.status != 'computing':
            continue
        point = points.get(c.depth_from)
        if not point:
            c.status = 'undecided'
            c.result = {'depth_m': c.depth_from, 'lithology': None,
                        'decided': False, 'reason': '测点已不存在，留待判', 'votes': [],
                        'referenced_registration_ids': []}
            c.reason = c.result['reason']
            continue
        lithology, result = _classify(point, regs, s)
        used = sorted(set(result['referenced_registration_ids']))
        s.execute(delete(LabCandidateRef).where(LabCandidateRef.candidate_id == c.id))
        for rid in used:
            s.add(LabCandidateRef(candidate_id=c.id, registration_id=rid))
        c.result = result
        c.status = 'pending' if lithology else 'undecided'
        c.reason = result['reason']
    decided = sum(1 for c in candidates if c.status == 'pending')
    x.evidence = {
        **evidence,
        'bag_count': len(regs),
        'candidate_count': len(candidates),
        'decided_count': decided,
        'undecided_count': len(candidates) - decided,
        'note': '同工区样袋物性投票，每袋只计一票；票数接近的测点留待判',
    }
    x.state = 'ready'
    x.error = ''


def _kill(s, x, reason):
    x.state = 'failed'
    x.error = reason
    x.source_invalidated = True
    x.invalid_reason = reason
    for c in s.scalars(
        select(LabCandidate)
        .where(LabCandidate.interpretation_id == x.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ):
        if c.status != 'confirmed':
            c.status = 'invalidated'
            c.reason = reason


def _refs_valid(s, c):
    refs = s.scalars(
        select(LabCandidateRef)
        .where(LabCandidateRef.candidate_id == c.id)
        .order_by(LabCandidateRef.registration_id)
    ).all()
    if not refs:
        raise HTTPException(409, '候选没有可核验的样袋证据，不能确认')
    for rf in refs:
        reg = s.scalar(
            select(LabRegistration)
            .where(LabRegistration.id == rf.registration_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not registration_valid(s, reg):
            raise HTTPException(409, '引用样袋已破签或化验已撤回，候选失效，不能确认')


def confirm_candidate(s, id, u):
    from .common import hole

    ref = get(s, LabCandidate, id)
    hole(s, ref.borehole_id)
    c = get(s, LabCandidate, id, True)
    if c.status == 'invalidated':
        raise HTTPException(409, '引用样袋已破签或化验已撤回，候选失效，不能确认')
    if c.status != 'pending':
        raise HTTPException(409, '仅待确认候选可确认')
    if not (c.result or {}).get('lithology'):
        raise HTTPException(409, '该测点岩性待判，须补充分化样袋或重新研判')
    _refs_valid(s, c)
    c.status = 'confirmed'
    c.decided_by = u['email']
    c.decided_at = now()
    audit(s, u, '确认化验岩性 ' + c.result['lithology'] + ' @ ' + str(c.depth_from) + 'm')
    return pack(c)


def confirm(s, id, u):
    from .common import hole

    ref = get(s, Interpretation, id)
    h = hole(s, ref.borehole_id)
    x = get(s, Interpretation, id, True)
    if s.scalar(
        select(Interpretation.id).where(
            Interpretation.borehole_id == x.borehole_id,
            Interpretation.kind == 'lab',
            Interpretation.state == 'confirmed',
            Interpretation.id != id,
        )
    ):
        raise HTTPException(409, '本孔已有主管确认的化验岩性解释，须先撤销旧解释')
    if x.state != 'ready':
        raise HTTPException(409, '须化验候选已计算就绪才能确认')
    cands = s.scalars(
        select(LabCandidate)
        .where(LabCandidate.interpretation_id == id)
        .order_by(LabCandidate.depth_from, LabCandidate.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if any(c.status == 'computing' for c in cands):
        raise HTTPException(409, '候选尚在计算，不能确认')
    if any(c.status == 'invalidated' for c in cands):
        raise HTTPException(409, '有候选引用的样袋已破签或化验已撤回，候选失效，不能确认')
    decided_idx = {
        i for i, c in enumerate(cands)
        if c.status in ('pending', 'confirmed') and (c.result or {}).get('lithology')
    }
    if not decided_idx:
        raise HTTPException(409, '全部测点待判，尚无可确认的化验岩性')
    for i in sorted(decided_idx):
        _refs_valid(s, cands[i])
    s.execute(delete(Stratum).where(Stratum.interpretation_id == id))
    edges = [0.0] + [
        (a.depth_from + b.depth_from) / 2
        for a, b in zip(cands, cands[1:])
    ] + [h.total_depth]
    # 连续同类测点合并成层；待判测点不生成岩性，其区间留给人工，不伪造层段。
    i = 0
    while i < len(cands):
        if i not in decided_idx:
            i += 1
            continue
        lithology = cands[i].result['lithology']
        j = i
        while j + 1 < len(cands) and (j + 1) in decided_idx and cands[j + 1].result['lithology'] == lithology:
            j += 1
        s.add(Stratum(
            interpretation_id=id,
            start_m=round(edges[i], 6),
            end_m=round(edges[j + 1], 6),
            lithology=lithology,
            contact_note='同工区化验样袋物性投票（每袋一票）判定，来源摘要 ' + str(x.source_digest)[:12],
        ))
        i = j + 1
    for i in decided_idx:
        if cands[i].status == 'pending':
            cands[i].status = 'confirmed'
            cands[i].decided_by = u['email']
            cands[i].decided_at = now()
    x.state = 'confirmed'
    audit(s, u, '主管确认化验岩性层段')
    return pack(x)


def propagate_invalid(s, registrations, reason, u):
    """破签/撤回后按引用精确失效候选；已确认解释保留证据，只标记来源失效。

    加锁顺序与计算/确认一致：解释 → 候选 → 登记，避免与迟到计算交叉等待。
    """
    regs = [r for r in registrations if r.state == 'registered']
    if not regs:
        return []
    ids = [r.id for r in regs]
    refs = s.scalars(
        select(LabCandidateRef).where(LabCandidateRef.registration_id.in_(ids))
    ).all()
    cand_ids = sorted({rf.candidate_id for rf in refs})
    cands = []
    if cand_ids:
        interp_ids = sorted(
            set(
                s.scalars(
                    select(LabCandidate.interpretation_id).where(
                        LabCandidate.id.in_(cand_ids)
                    )
                ).all()
            )
            - {None}
        )
        for iid in interp_ids:
            get(s, Interpretation, iid, True)
        cands = s.scalars(
            select(LabCandidate)
            .where(LabCandidate.id.in_(cand_ids))
            .order_by(LabCandidate.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    # 锁住登记行后再置失效，与计算中 validate 的读一致。
    locked = s.scalars(
        select(LabRegistration)
        .where(LabRegistration.id.in_(ids))
        .order_by(LabRegistration.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    for r in locked:
        if r.state == 'registered':
            r.state = 'invalidated'
            r.invalid_reason = reason
    if not cand_ids:
        return []
    for c in cands:
        if c.status != 'invalidated':
            c.status = 'invalidated'
            c.reason = reason
            c.decided_by = u['email']
            c.decided_at = now()
    for iid in sorted({c.interpretation_id for c in cands if c.interpretation_id}):
        x = get(s, Interpretation, iid, True)
        x.source_invalidated = True
        x.invalid_reason = reason
        if x.state == 'confirmed':
            # 已确认解释继续可查原证据，只标记来源失效；不悄悄改写既有层段。
            continue
        x.state = 'failed'
        x.error = '引用样袋已破签或化验已撤回，候选失效：' + reason
        for c in s.scalars(
            select(LabCandidate)
            .where(LabCandidate.interpretation_id == iid)
            .order_by(LabCandidate.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ):
            if c.status != 'confirmed':
                c.status = 'invalidated'
                c.reason = reason
    audit(s, u, '化验来源失效，引用候选作废')
    return cand_ids


def registrations_for_seal(s, seal_id):
    return s.scalars(
        select(LabRegistration).where(
            LabRegistration.seal_id == seal_id,
            LabRegistration.state == 'registered',
        )
    ).all()


def withdraw(s, id, p, u):
    ref = get(s, LabRegistration, id)
    from .common import hole

    hole(s, ref.borehole_id)
    # 不预先锁登记行：统一按 解释 → 候选 → 登记 的顺序，避免与迟到计算交叉等待。
    reg = get(s, LabRegistration, id, True)
    if reg.state != 'registered':
        raise HTTPException(409, '该化验登记已失效，无需重复撤回')
    propagate_invalid(s, [reg], '化验登记已撤回：' + p.reason, u)
    audit(s, u, '撤回化验登记 ' + str(reg.sample_id))
    return pack(reg)


def evidence_report(s, id):
    """层段解释页面可查：引用了哪些样袋、判得什么岩性、哪些测点为何待判。"""
    x = get(s, Interpretation, id)
    strata = s.scalars(
        select(Stratum).where(Stratum.interpretation_id == id).order_by(Stratum.start_m)
    ).all()
    cands = s.scalars(
        select(LabCandidate)
        .where(LabCandidate.interpretation_id == id)
        .order_by(LabCandidate.depth_from, LabCandidate.id)
    ).all()
    regs = {
        r.id: r
        for r in s.scalars(select(LabRegistration).order_by(LabRegistration.id))
    }
    samples = {
        v.id: v for v in s.scalars(select(SampleInterval).order_by(SampleInterval.id))
    }
    seals = {v.id: v for v in s.scalars(select(Seal).order_by(Seal.id))}

    def bag_row(rid):
        r = regs.get(rid)
        if not r:
            return {'registration_id': rid, 'state': 'missing'}
        sample = samples.get(r.sample_id)
        seal = seals.get(r.seal_id)
        return {
            'registration_id': r.id,
            'sample_id': r.sample_id,
            'sample_code': sample.code if sample else '',
            'seal_id': r.seal_id,
            'seal_code': seal.code if seal else '',
            'lithology': r.lithology,
            'basis': r.basis,
            'state': r.state,
            'invalid_reason': r.invalid_reason,
            'work_area': r.work_area,
            'readings': r.readings,
            'valid': registration_valid(s, r),
        }

    bags_by_cand = {}
    for rf in s.scalars(
        select(LabCandidateRef).where(
            LabCandidateRef.candidate_id.in_([c.id for c in cands] or [0])
        )
    ):
        bags_by_cand.setdefault(rf.candidate_id, []).append(rf.registration_id)

    candidate_rows = [
        {
            'id': c.id,
            'depth_from': c.depth_from,
            'depth_to': c.depth_to,
            'status': c.status,
            'lithology': (c.result or {}).get('lithology'),
            'reason': c.reason,
            'votes': (c.result or {}).get('votes', []),
            'bags': [bag_row(rid) for rid in bags_by_cand.get(c.id, [])],
        }
        for c in cands
    ]
    pool = list((x.evidence or {}).get('pool_registration_ids') or [])
    return {
        'interpretation': pack(x),
        'strata': [pack(v) for v in strata],
        'candidates': candidate_rows,
        'pending_count': sum(1 for c in cands if c.status in ('pending', 'undecided')),
        'undecided': [
            {'depth_m': c.depth_from, 'reason': c.reason}
            for c in cands
            if c.status in ('pending', 'undecided')
        ],
        'bags': [bag_row(rid) for rid in pool],
        'source_invalidated': x.source_invalidated,
        'invalid_reason': x.invalid_reason,
    }

