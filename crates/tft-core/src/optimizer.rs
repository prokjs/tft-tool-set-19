use crate::model::{is_tank, star_text, Champion, GameData, OptimizeOptions};
use serde_json::{json, Value};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap};

pub const BAND_TRAITS: &[&str] = &["Beach Pop", "Classical", "EDM", "Gu Feng", "Heartsteel", "Hyperpop", "Illbeats", "Ionic Spark", "Jazz", "K/DA", "Pentakill", "R&B", "Rock", "True Damage"];
pub const FRONTLINE_TRAITS: &[&str] = &["Bodyguard", "Juggernaut", "Frontman"];
pub fn trait_value(tier: usize, mode: &str) -> f64 {
    if mode == "cap_out" { return 18.0 * tier as f64; }
    if mode == "synergies" { 100.0 * usize::from(tier > 0) as f64 + 10.0 * tier as f64 }
    else { 110.0 * tier as f64 + 40.0 * tier as f64 * tier.saturating_sub(1) as f64 }
}
pub fn jazz_bonus(active: usize, jazz_tier: usize, bridge: bool, mode: &str) -> f64 {
    if mode == "cap_out" && jazz_tier > 0 { 30.0 * active as f64 * (1.0 + 0.5 * jazz_tier.saturating_sub(1) as f64) }
    else if bridge && jazz_tier > 0 && matches!(mode, "balanced" | "high_value") { 20.0 * active as f64 * jazz_tier as f64 } else { 0.0 }
}
pub fn frontline_anchor_value(champion: &Champion, stars: u8, level: usize) -> f64 {
    if level < 10 || !is_tank(champion) { return 0.0; }
    if (1..=3).contains(&champion.cost) && stars >= 3 { 450.0 + 150.0 * (stars - 3) as f64 }
    else if champion.cost >= 4 && stars >= 2 { 350.0 + 25.0 * (champion.cost - 4) as f64 + 150.0 * (stars - 2) as f64 } else { 0.0 }
}
pub fn trait_context_adjustment(name: &str, tier: usize, count: usize, mode: &str, level: usize, carry_ready: bool, hype_ready: bool) -> f64 {
    if matches!(mode, "synergies" | "cap_out") { return 0.0; }
    if (name == "Gu Feng" && count < 4) || (name == "True Damage" && count < 5 && !carry_ready) || (name == "Hyperpop" && count < 4 && level >= 10 && !hype_ready) { let base = trait_value(tier, mode); base * 0.35 - base } else { 0.0 }
}
pub(crate) fn late_carry_value(flags: u8, level: usize, mode: &str) -> f64 {
    if level >= 10 && matches!(mode, "balanced" | "high_value") && crate::guidance::has_late_carry(flags) { 300.0 } else { 0.0 }
}
pub fn unit_strategy_value(champion: &Champion, stars: u8, level: usize, mode: &str) -> f64 {
    if mode == "cap_out" {
        let premium = match champion.cost { 4 => 110.0, 5 => 190.0, 7 => 260.0, _ => 0.0 };
        return 18.0 * crate::guidance::planned_unit_strength(champion, stars) + premium;
    }
    if mode == "synergies" { return 0.0; }
    let mut value = if champion.name == "Senna" { -350.0 } else { 0.0 };
    if level >= 10 { if champion.cost <= 3 && stars == 3 { value -= 100.0; } if champion.name == "Qiyana" && stars == 2 { value -= 60.0; } }
    value
}

// Combat scope comes from the bundled English trait descriptions, not the
// number or colour of badges. Unlisted uniques (including Beat Master) are
// self/economy effects; Composer needs instruments we cannot assume are owned.
fn cap_trait_value(name: &str, tier: usize, unique: bool) -> f64 {
    if tier == 0 { return 0.0; }
    let extra = tier.saturating_sub(1) as f64;
    match name {
        "Jazz" => 0.0, // Count active nonunique traits once, after all counts.
        "R&B" => 220.0 + 40.0 * extra,
        "Bodyguard" | "Frontman" => 150.0 + 50.0 * extra,
        "Arcanist" | "Prodigy" | "Swiftstriker" => 140.0 + 45.0 * extra,
        "Choreographer" => 155.0 + 50.0 * extra,
        "Illbeats" => 130.0 + 50.0 * extra,
        "Heartsteel" => 95.0 + 45.0 * extra,
        "Beach Pop" => 90.0 + 55.0 * extra,
        "Disco" => 220.0,
        "Melodic Heart" => 100.0,
        "One Monster Band" => 120.0,
        _ if unique => 0.0,
        _ => trait_value(tier, "cap_out"),
    }
}
pub fn planned_stars(data: &GameData, level: usize, requested: &BTreeMap<String, u8>, sona_band: &str) -> Result<BTreeMap<String, u8>, String> {
    planned_path_stars(data,level,requested,sona_band,&BTreeSet::new(),false)
}

pub(crate) fn reroll_plan(data:&GameData,goal:&[String],requested:&BTreeMap<String,u8>)->bool {
    !goal.is_empty() && (!goal.iter().any(|n|n=="Sona")
        || goal.iter().filter(|n|data.champions.get(*n).is_some_and(|c|c.cost<=2)).count()*2>goal.len()
        || requested.iter().any(|(n,&rank)|goal.contains(n)&&rank>=3&&data.champions.get(n).is_some_and(|c|c.cost<=3)))
}

pub(crate) fn planned_path_stars(data:&GameData,level:usize,requested:&BTreeMap<String,u8>,_sona_band:&str,keepers:&BTreeSet<String>,reroll:bool)->Result<BTreeMap<String,u8>,String> {
    if level==0 {return Err("level must be an integer >= 1.".into());}
    for (name,rank) in requested {
        if !data.champions.contains_key(name) {return Err("stars contains an unknown champion.".into());}
        if !(1..=4).contains(rank) {return Err("Star levels must be between 1 and 4.".into());}
    }
    Ok(data.champions.iter().map(|(name,c)|{
        let keeper=keepers.contains(name);
        let two_at=match c.cost {1=>if keeper {4} else {5},2=>6,3=>7,4=>8,5=>10,_=>usize::MAX};
        let three_at=match c.cost {1=>5,2=>6,3=>7,_=>usize::MAX};
        let automatic=if reroll&&keeper&&level>=three_at {3} else if level>=two_at {2} else {1};
        (name.clone(),requested.get(name).copied().unwrap_or(automatic))
    }).collect())
}

struct Unit<'a> {
    champion: &'a Champion, contributions: Vec<(usize, usize)>, group: Option<usize>, collab: bool,
    frontline: bool, direct: bool, td: bool, hype: bool, anchor: f64, strategy: f64, carry_flags: u8,
}
struct Context<'a> { data: &'a GameData, traits: Vec<&'a str>, units: Vec<Unit<'a>>, links: Vec<Vec<bool>>, mode: &'a str, level: usize }
impl<'a> Context<'a> {
    fn new(data: &'a GameData, collab: &[String], mode: &'a str, band: &str, stars: &BTreeMap<String,u8>, level: usize) -> Result<Self,String> {
        let traits: Vec<_> = data.traits.keys().map(String::as_str).collect();
        let groups: BTreeSet<_> = data.champions.values().filter_map(|c| c.exclusive_group.as_deref()).collect();
        let groups: BTreeMap<_,_> = groups.into_iter().enumerate().map(|(i,g)|(g,i)).collect();
        if (!collab.is_empty() || data.champions.contains_key("Sona")) && !data.traits.contains_key("Collab") { return Err("Collab must be defined when Collab champions are present.".into()); }
        let mut units = Vec::new();
        for c in data.champions.values() {
            let dynamic = collab.contains(&c.name) || c.name == "Sona";
            let mut native: BTreeSet<&str> = c.traits.iter().map(String::as_str).filter(|t| *t != "Collab").collect();
            if dynamic { native.insert("Collab"); } if c.name == "Sona" && !band.is_empty() { native.insert(band); }
            let rank = stars[&c.name];
            let td = data.traits.contains_key("True Damage") && native.contains("True Damage") && c.name != "Senna" && ((c.name == "Qiyana" && rank >= 2) || (c.name == "Ekko" && rank >= 3) || (c.cost >= 4 && rank >= 2));
            let hype = data.traits.contains_key("Hyperpop") && native.contains("Hyperpop") && rank >= 2;
            let contributions = native.iter().map(|t| { let i = traits.binary_search(t).map_err(|_| format!("Unknown trait: {t}."))?; Ok((i, if data.traits[*t].count_mode == "stars" { rank as usize } else { 1 })) }).collect::<Result<Vec<_>,String>>()?;
            units.push(Unit { champion:c, group:c.exclusive_group.as_deref().map(|g| groups[g]), collab:dynamic, frontline:is_tank(c) || native.iter().any(|t| FRONTLINE_TRAITS.contains(t)), direct:dynamic && (c.traits.iter().any(|t| t == "Jazz") || (c.name == "Sona" && band == "Jazz")), td,hype,anchor:frontline_anchor_value(c,rank,level),strategy:unit_strategy_value(c,rank,level,mode),carry_flags:crate::guidance::carry_flags(c,rank),contributions });
        }
        let mut links = vec![vec![false;units.len()];units.len()];
        if data.traits.contains_key("Jazz") { for (i,a) in units.iter().enumerate() { if !a.champion.traits.iter().any(|t| t == "Jazz") && !(a.champion.name == "Sona" && band == "Jazz") { continue; } for (j,b) in units.iter().enumerate() { if i == j || !b.collab { continue; } if a.champion.traits.iter().any(|t| t != "Jazz" && t != "Collab" && !data.traits[t].unique && b.champion.traits.contains(t)) { links[i][j] = true; links[j][i] = true; } } } }
        Ok(Self {data,traits,units,links,mode,level})
    }
    fn metrics(&self, ids: &[usize], counts: &mut [usize]) -> Metrics {
        counts.fill(0);
        let mut m = Metrics::default();
        let mut td = false; let mut hype = false; let mut bridge = false;
        for (position,&id) in ids.iter().enumerate() { let u=&self.units[id]; m.carry_flags |= u.carry_flags; td |= u.td; hype |= u.hype; bridge |= u.direct || ids[..position].iter().any(|&other| self.links[id][other]); m.frontline += usize::from(u.frontline); m.cost += u.champion.cost; m.collab += usize::from(u.collab); m.anchor=m.anchor.max(u.anchor); m.strategy+=u.strategy; for &(t,n) in &u.contributions { counts[t]+=n; } }
        let mut jazz=0;
        for (i,&count) in counts.iter().enumerate() { let name=self.traits[i]; let t=&self.data.traits[name]; let tier=t.breakpoints.partition_point(|&p| p<=count); if t.unique { m.unique+=usize::from(tier>0); if self.mode=="cap_out" {m.value+=cap_trait_value(name,tier,true);} } else { m.active+=usize::from(tier>0); m.tiers+=tier; m.value+=if self.mode=="cap_out" {cap_trait_value(name,tier,false)} else {trait_value(tier,self.mode)+trait_context_adjustment(name,tier,count,self.mode,self.level,td,hype)}; if let Some(&next)=t.breakpoints.get(tier) { let prev=if tier>0 {t.breakpoints[tier-1]} else {0}; m.progress+=(count-prev) as f64/(next-prev) as f64; } } if name=="Jazz" {jazz=tier;} }
        m.score=m.value+if self.mode=="cap_out" {0.0} else {m.unique as f64}+m.strategy+jazz_bonus(m.active,jazz,bridge,self.mode)+late_carry_value(m.carry_flags,self.level,self.mode);
        if matches!(self.mode,"balanced"|"high_value") {m.score+=m.anchor+30.0*m.frontline.min(2) as f64+20.0*usize::from(ids.len()>m.frontline) as f64;}
        if self.mode=="cap_out" {m.score+=180.0*m.frontline.min(2) as f64+240.0*usize::from(crate::guidance::has_late_carry(m.carry_flags)) as f64;}
        if self.mode=="high_value" {m.score+=30.0*m.cost as f64;} m
    }
}
#[derive(Default,Clone,Copy)]
struct Metrics { active:usize, tiers:usize, unique:usize, frontline:usize, cost:usize, collab:usize, anchor:f64, strategy:f64, value:f64, progress:f64, score:f64, carry_flags:u8 }

pub fn evaluate_team(data:&GameData,names:&[String],collab:&[String],mode:&str,sona_band:&str,requested:&BTreeMap<String,u8>,level:Option<usize>)->Result<Value,String> {
    for name in names {if !data.champions.contains_key(name) {return Err(format!("Unknown champion: {name}."));}}
    let level=level.unwrap_or_else(||names.iter().map(|n|data.champions[n].slots).sum());
    let keepers=names.iter().cloned().collect();
    let stars=planned_path_stars(data,level,requested,sona_band,&keepers,mode!="cap_out"&&reroll_plan(data,names,requested))?;
    let ctx=Context::new(data,collab,mode,sona_band,&stars,level)?;
    let ids=names.iter().map(|n|ctx.units.binary_search_by(|u|u.champion.name.cmp(n)).unwrap()).collect::<Vec<_>>();
    let mut counts=vec![0;ctx.traits.len()]; let m=ctx.metrics(&ids,&mut counts);
    let mut notes=Vec::new(); let mut anchor:Option<&str>=None; let mut merit=0.0;
    for name in names { let c=&data.champions[name]; let rank=stars[name];
        if level>=8 && c.cost==4 && rank==2 && is_tank(c) && !requested.contains_key(name) {notes.push(format!("Automatic tank upgrade target: {name} {} (planned, not owned).",star_text(rank)));}
        if matches!(mode,"balanced"|"high_value") {let value=frontline_anchor_value(c,rank,level); if value>merit || (value>0.0 && value==merit && anchor.is_some_and(|old|name.as_str()<old)) {merit=value;anchor=Some(name);} if name=="Senna" {notes.push("Senna receives a current-patch preference penalty, not a measured winrate estimate.".into());}}
        if c.cost<=3 && rank==3 {let tax=if level>=10 && matches!(mode,"balanced"|"high_value") {"; fast-10 reroll-path penalty applies"} else {""};notes.push(format!("Planned reroll target: {name} {}{tax}; not assumed owned.",star_text(rank)));}
        if name=="Qiyana" && rank==2 && level>=10 && matches!(mode,"balanced"|"high_value") {notes.push("Qiyana ★★ is a planned target with a fast-10 path penalty, not an owned upgrade.".into());}
    }
    let td=ids.iter().any(|&i|ctx.units[i].td); let hype=ids.iter().any(|&i|ctx.units[i].hype);
    let mut rows=Vec::new();
    for (i,&count) in counts.iter().enumerate() {if count==0 {continue;} let name=ctx.traits[i];let t=&data.traits[name];let tier=t.breakpoints.partition_point(|&p|p<=count);
        if !t.unique && trait_context_adjustment(name,tier,count,mode,level,td,hype)<0.0 {let requirement=match name {"Gu Feng"=>"four members","True Damage"=>"five members or an upgraded non-Senna carry",_=>"four members or an upgraded Hyperpop member"}; notes.push(format!("{name} splash value is discounted without {requirement}."));}
        rows.push(json!({"name":name,"count":count,"tier":if tier>0 {t.breakpoints[tier-1]} else {0},"next":t.breakpoints.get(tier),"unique":t.unique}));
    }
    let mut sorted=names.to_vec();sorted.sort();let board_stars:BTreeMap<_,_>=sorted.iter().map(|n|(n.clone(),stars[n])).collect();
    if mode=="cap_out" {
        notes.push("Cap-out is a premium-unit, planned-rank and combat-utility heuristic, not measured strength or win rate. Established reroll carries can be stronger than unupgraded premium hits.".into());
        for row in &rows {
            let name=row["name"].as_str().unwrap();
            let t=&data.traits[name];
            if row["tier"].as_u64().unwrap_or(0)>0 && (name=="Jazz" || cap_trait_value(name,1,t.unique)>trait_value(1,"cap_out")) {
                notes.push(format!("{name}: {} Source: {}",t.description,t.description_source));
            }
        }
    }
    Ok(json!({"champions":sorted,"traits":rows,"active":m.active,"tiers":m.tiers,"frontline":m.frontline,"cost":m.cost,"score":m.score,"planned_stars":board_stars,"frontline_anchor":anchor,"strategy_notes":notes}))
}

// Every board has at most eleven units. States are stack-sized and contain no
// strings, JSON, heap-backed counts, or per-candidate allocations.
#[derive(Clone,Copy)]
struct State { picks:[usize;11], len:usize, metrics:Metrics }
#[derive(Clone,Copy)]
struct Entry { state:State, rank:f64, cost_rank:i128, serial:usize }
impl Entry {fn cmp_rank(&self,o:&Self)->Ordering { self.rank.total_cmp(&o.rank).then(self.state.metrics.active.cmp(&o.state.metrics.active)).then(self.state.metrics.tiers.cmp(&o.state.metrics.tiers)).then(self.cost_rank.cmp(&o.cost_rank)).then(o.serial.cmp(&self.serial)) }}
impl PartialEq for Entry {fn eq(&self,o:&Self)->bool {self.cmp_rank(o)==Ordering::Equal}}
impl Eq for Entry {}
impl PartialOrd for Entry {fn partial_cmp(&self,o:&Self)->Option<Ordering>{Some(self.cmp(o))}}
impl Ord for Entry {fn cmp(&self,o:&Self)->Ordering {o.cmp_rank(self)}}

type FeasibilityKey=(usize,usize,[usize;11]);
fn attainable(ctx:&Context<'_>,optional:&[usize],start:usize,budget:usize,blocked:[usize;11],cache:&mut HashMap<FeasibilityKey,isize>)->isize {
    if budget==0 {return 0;} if start==optional.len() {return -1;}
    let key=(start,budget,blocked);if let Some(&value)=cache.get(&key) {return value;}
    let mut best=attainable(ctx,optional,start+1,budget,blocked,cache);let u=&ctx.units[optional[start]];
    if u.champion.slots<=budget && !u.group.is_some_and(|g|blocked.contains(&g)) {let mut next=blocked;if let Some(g)=u.group {let at=next.iter().position(|&x|x==usize::MAX).unwrap();next[at]=g;next.sort_unstable();}let following=attainable(ctx,optional,start+1,budget-u.champion.slots,next,cache);if following>=0 {best=best.max(following+isize::from(u.collab));}}
    cache.insert(key,best);best
}
fn push(heaps:&mut [BinaryHeap<Entry>],slots:usize,state:State,options:&OptimizeOptions,serial:&mut usize) {
    *serial+=1;let e=Entry {rank:state.metrics.score+if slots<options.size {6.0*state.metrics.progress} else {0.0},cost_rank:if options.mode=="high_value" {state.metrics.cost as i128} else {-(state.metrics.cost as i128)},serial:*serial,state};
    let width=256.max(1024.min(options.limit.saturating_mul(24)));let capacity=if slots==options.size {width.max(options.limit)} else {width};let heap=&mut heaps[slots];
    if heap.len()<capacity {heap.push(e);} else if e.cmp_rank(heap.peek().unwrap())==Ordering::Greater {heap.pop();heap.push(e);}
}

// Cap acquisition ranks are supplied explicitly by cap_out. Item matching is
// finite and shared with emitted assignments; it participates in every beam node.
struct TeamScoring<'a> {
    options: &'a OptimizeOptions,
    items: Option<&'a crate::cap_items::PreparedItems>,
    individual_fit: Vec<f64>,
}
impl<'a> TeamScoring<'a> {
    fn new(options:&'a OptimizeOptions,items:Option<&'a crate::cap_items::PreparedItems>,count:usize)->Self {
        Self {options,items,individual_fit:items.map(|items|(0..count).map(|i|items.score(&[i])).collect()).unwrap_or_default()}
    }
    fn apply(&self,ctx:&Context<'_>,ids:&[usize],m:&mut Metrics) {
        if let Some(items)=self.items {
            let fit=items.score(ids);
            m.score+=35.0*fit;
            let mut carry_fit=0.0_f64;
            let mut managers=0;
            for &id in ids {
                let c=ctx.units[id].champion;
                if c.traits.iter().any(|t|t=="Manager") {managers+=1;}
                if matches!(crate::guidance::item_profile(c),"ad"|"ap"|"flex") {carry_fit=carry_fit.max(self.individual_fit[id]);}
                // Known upgraded combat anchors are costly to dismantle. New
                // hypothetical hits have rank one, so receive no such credit.
                if c.cost>=4 && self.options.stars.get(&c.name).copied().unwrap_or(1)>=2
                    && (is_tank(c)||self.individual_fit[id]>=8.0) {m.score+=80.0;}
            }
            // Manager buffs a Spotlight ally, not the entire board. Only count
            // it when this board has an actual compatible offensive item user.
            if managers>=2 && carry_fit>=8.0 {m.score+=180.0+if managers>=3 {60.0} else {0.0};}
        }
        if self.options.no_ad_items==self.options.no_ap_items {return;}
        let wanted=if self.options.no_ad_items {"ap"} else {"ad"};
        let mut best=0.0_f64;
        for &id in ids {
            let c=ctx.units[id].champion;
            let profile=crate::guidance::item_profile(c);
            if profile==wanted || profile=="flex" {
                let rank=self.options.stars.get(&c.name).copied().unwrap_or(1);
                best=best.max(600.0+10.0*crate::guidance::planned_unit_strength(c,rank).min(30.0));
            } else if matches!(profile,"ad"|"ap") {m.score-=180.0;}
        }
        m.score+=best;
    }
}

pub(crate) fn evaluate_cap_item_team(data:&GameData,names:&[String],options:&OptimizeOptions,items:&crate::cap_items::PreparedItems)->Result<Value,String> {
    let mut board=evaluate_team(data,names,&options.collab,"cap_out",&options.sona_band,&options.stars,Some(options.size))?;
    let stars=planned_stars(data,options.size,&options.stars,&options.sona_band)?;
    let ctx=Context::new(data,&options.collab,"cap_out",&options.sona_band,&stars,options.size)?;
    let ids=names.iter().map(|n|ctx.units.binary_search_by(|u|u.champion.name.cmp(n)).unwrap()).collect::<Vec<_>>();
    let mut m=ctx.metrics(&ids,&mut vec![0;ctx.traits.len()]);
    TeamScoring::new(options,Some(items),ctx.units.len()).apply(&ctx,&ids,&mut m);
    board["score"]=json!(m.score);
    board["item_fit"]=json!({"score":items.score(&ids),"weighted_score":35.0*items.score(&ids)});
    Ok(board)
}

pub(crate) fn optimize_with_cap_items(data:&GameData,options:&OptimizeOptions,items:&crate::cap_items::PreparedItems)->Result<Vec<Value>,String> {
    optimize_scored(data,options,Some(items))
}
pub fn optimize(data:&GameData,options:&OptimizeOptions)->Result<Vec<Value>,String> {
    optimize_scored(data,options,None)
}
fn optimize_scored(data:&GameData,options:&OptimizeOptions,items:Option<&crate::cap_items::PreparedItems>)->Result<Vec<Value>,String> {
    options.validate(data)?;
    if !options.sona_band.is_empty() && (!BAND_TRAITS.contains(&options.sona_band.as_str()) || !data.traits.contains_key(&options.sona_band)) {return Err("sona_band must be empty or a valid band trait.".into());}
    let stars=planned_stars(data,options.size,&options.stars,&options.sona_band)?;let ctx=Context::new(data,&options.collab,&options.mode,&options.sona_band,&stars,options.size)?;
    let scoring=TeamScoring::new(options,items,ctx.units.len());
    let fixed:Vec<_>=ctx.units.iter().enumerate().filter(|(_,u)|options.locked.contains(&u.champion.name)).map(|(i,_)|i).collect();
    let optional:Vec<_>=ctx.units.iter().enumerate().filter(|(_,u)|!options.locked.contains(&u.champion.name) && !options.excluded.contains(&u.champion.name)).map(|(i,_)|i).collect();
    let occupied:usize=fixed.iter().map(|&i|ctx.units[i].champion.slots).sum();if occupied>options.size {return Err("Locked champions exceed the slot budget.".into());}
    let mut blocked=[usize::MAX;11];for &id in &fixed {if let Some(g)=ctx.units[id].group {if blocked.contains(&g) {return Err("Locked champions include mutually exclusive variants.".into());}let at=blocked.iter().position(|&x|x==usize::MAX).unwrap();blocked[at]=g;}}blocked.sort_unstable();
    let fixed_collab=fixed.iter().filter(|&&id|ctx.units[id].collab).count();let mut cache=HashMap::new();
    if attainable(&ctx,&optional,0,options.size-occupied,blocked,&mut cache)<options.min_collab.saturating_sub(fixed_collab) as isize {return Err("No team can fill the slots while satisfying the Collab minimum.".into());}
    let mut counts=vec![0;ctx.traits.len()];let mut metrics=ctx.metrics(&fixed,&mut counts);scoring.apply(&ctx,&fixed,&mut metrics);let initial=State {picks:[0;11],len:0,metrics};
    let mut heaps:Vec<BinaryHeap<Entry>>=(0..=options.size).map(|_|BinaryHeap::new()).collect();let mut serial=0;push(&mut heaps,occupied,initial,options,&mut serial);
    let mut ids=[0;11];ids[..fixed.len()].copy_from_slice(&fixed);
    for filled in occupied..options.size {
        let mut entries=std::mem::take(&mut heaps[filled]).into_vec();entries.sort_unstable_by(|a,b|b.cmp_rank(a));
        for entry in entries {let state=entry.state;let mut current_blocked=blocked;for &p in &state.picks[..state.len] {if let Some(g)=ctx.units[optional[p]].group {let at=current_blocked.iter().position(|&x|x==usize::MAX).unwrap();current_blocked[at]=g;}}current_blocked.sort_unstable();
            let start=if state.len>0 {state.picks[state.len-1]+1} else {0};
            for index in start..optional.len() {let u=&ctx.units[optional[index]];let next_filled=filled+u.champion.slots;if next_filled>options.size || u.group.is_some_and(|g|current_blocked.contains(&g)) {continue;}let mut next_blocked=current_blocked;if let Some(g)=u.group {let at=next_blocked.iter().position(|&x|x==usize::MAX).unwrap();next_blocked[at]=g;next_blocked.sort_unstable();}
                let needed=options.min_collab.saturating_sub(state.metrics.collab+usize::from(u.collab));if attainable(&ctx,&optional,index+1,options.size-next_filled,next_blocked,&mut cache)<needed as isize {continue;}
                let mut next=state;next.picks[next.len]=index;next.len+=1;for (j,&p) in next.picks[..next.len].iter().enumerate() {ids[fixed.len()+j]=optional[p];}let team=&ids[..fixed.len()+next.len];next.metrics=ctx.metrics(team,&mut counts);scoring.apply(&ctx,team,&mut next.metrics);push(&mut heaps,next_filled,next,options,&mut serial);
            }
        }
    }
    let team_ids=|s:&State| {let mut board=[usize::MAX;11];board[..fixed.len()].copy_from_slice(&fixed);for (j,&p) in s.picks[..s.len].iter().enumerate(){board[fixed.len()+j]=optional[p];}board.sort_unstable();board};
    let compare=|a:&State,b:&State|b.metrics.score.total_cmp(&a.metrics.score).then(b.metrics.active.cmp(&a.metrics.active)).then(b.metrics.tiers.cmp(&a.metrics.tiers)).then_with(||if options.mode=="high_value" {b.metrics.cost.cmp(&a.metrics.cost)} else {a.metrics.cost.cmp(&b.metrics.cost)}).then_with(||team_ids(a).cmp(&team_ids(b)));
    let mut candidates:Vec<_>=std::mem::take(&mut heaps[options.size]).into_vec().into_iter().map(|e|e.state).collect();
    if options.no_ad_items!=options.no_ap_items {
        let wanted=if options.no_ad_items {"ap"} else {"ad"};
        candidates.retain(|state|team_ids(state).iter().copied().filter(|&i|i!=usize::MAX).any(|i|{
            let profile=crate::guidance::item_profile(ctx.units[i].champion);profile==wanted || profile=="flex"
        }));
    }
    candidates.sort_by(compare);
    let mut penalties=vec![0.0_f64;candidates.len()];let mut remaining:Vec<_>=(0..candidates.len()).collect();let mut chosen=Vec::new();
    while !remaining.is_empty() && chosen.len()<options.limit {let position=(0..remaining.len()).max_by(|&a,&b| {let ia=remaining[a];let ib=remaining[b];(candidates[ia].metrics.score-penalties[ia]).total_cmp(&(candidates[ib].metrics.score-penalties[ib])).then(ib.cmp(&ia))}).unwrap();let selected=candidates[remaining.remove(position)];for &i in &remaining {let other=&candidates[i];let overlap=selected.picks[..selected.len].iter().filter(|p|other.picks[..other.len].contains(p)).count() as f64/selected.len.max(other.len).max(1) as f64;penalties[i]=penalties[i].max(30.0*overlap);}chosen.push(selected);}
    let mut boards:Vec<Value>=chosen.iter().map(|s| {
        let ids=team_ids(s);let ids=ids.as_slice();let ids=&ids[..fixed.len()+s.len];
        let names=ids.iter().map(|&i|ctx.units[i].champion.name.clone()).collect::<Vec<_>>();
        if let Some(items)=items {return evaluate_cap_item_team(data,&names,options,items);}
        let mut board=evaluate_team(data,&names,&options.collab,&options.mode,&options.sona_band,&options.stars,Some(options.size))?;
        if options.no_ad_items!=options.no_ap_items {
            let mut adjustment=Metrics::default();scoring.apply(&ctx,ids,&mut adjustment);
            board["score"]=json!(board["score"].as_f64().unwrap()+adjustment.score);
        }
        Ok(board)
    }).collect::<Result<_,String>>()?;
    if options.no_ad_items!=options.no_ap_items {
        let wanted=if options.no_ad_items {"ap"} else {"ad"};
        boards.retain(|board|board["champions"].as_array().unwrap().iter().any(|n|matches!(crate::guidance::item_profile(&data.champions[n.as_str().unwrap()]),"flex") || crate::guidance::item_profile(&data.champions[n.as_str().unwrap()])==wanted));
        if boards.is_empty() {return Err("The locked roster, exclusions or slot budget leave no carry compatible with the selected NO AD items / NO AP items alternative. Keep tank/support locks, or free a carry slot and try again.".into());}
    }
    // The beam uses universal progression. Real goal-dependent reroll ranks can
    // alter K/DA counts, anchors and scores, so never return that provisional order.
    boards.sort_by(|a,b|b["score"].as_f64().unwrap().total_cmp(&a["score"].as_f64().unwrap())
        .then(b["active"].as_u64().cmp(&a["active"].as_u64()))
        .then(b["tiers"].as_u64().cmp(&a["tiers"].as_u64()))
        .then_with(||if options.mode=="high_value" {b["cost"].as_u64().cmp(&a["cost"].as_u64())} else {a["cost"].as_u64().cmp(&b["cost"].as_u64())})
        .then_with(||a["champions"].as_array().unwrap().iter().map(|n|n.as_str().unwrap()).cmp(b["champions"].as_array().unwrap().iter().map(|n|n.as_str().unwrap()))));
    Ok(boards)
}
