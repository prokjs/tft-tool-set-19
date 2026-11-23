//! Conditional, target-aware planning. Tasks are opportunities, never simulated progress.
use crate::model::{is_tank, Champion, GameData, OptimizeOptions};
use crate::{guidance, optimizer, positioning, trait_support};
use regex::Regex;
use serde_json::{json, Map, Value};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

static DISTINCT:LazyLock<Regex>=LazyLock::new(||Regex::new(r"^Have (\d+) (?:unique )?(.+?) champions\.$").unwrap());
static STAR_LEVELS:LazyLock<Regex>=LazyLock::new(||Regex::new(r"(\d+) (?:unique )?star levels of (.+?)(?: champions\.| without|\.|$)").unwrap());
static ITEM_HOLDER:LazyLock<Regex>=LazyLock::new(||Regex::new(r"Have (?:a|an) (.+?) champion holding").unwrap());
static HOLDER_SEPARATOR:LazyLock<Regex>=LazyLock::new(||Regex::new(r",?\s+or\s+|,\s*").unwrap());
static STAR_SEPARATOR:LazyLock<Regex>=LazyLock::new(||Regex::new(r" and | or ").unwrap());
static WITHOUT:LazyLock<Regex>=LazyLock::new(||Regex::new(r"(?i)\bwithout\b").unwrap());

pub const SHOP_SOURCE: &str = "https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table";
pub(crate) const SHOP_ODDS: [[usize; 5]; 11] = [[100,0,0,0,0],[100,0,0,0,0],[75,25,0,0,0],[55,30,15,0,0],[45,33,20,2,0],[30,40,25,5,0],[16,30,43,10,1],[15,20,32,30,3],[10,17,25,33,15],[5,10,20,40,25],[1,2,12,50,35]];
const RESULT_KEYS: &[&str] = &["champions","traits","active","tiers","frontline","cost","score","planned_stars","frontline_anchor","strategy_notes"];
type Names = BTreeSet<usize>;

fn maximum_setup(p:&Planner,names:&Names,weight:impl Fn(usize)->usize)->usize{
    let size=p.options.size;let mut best=[0;12];
    for &i in names.iter().filter(|i|p.groups[**i]==0){
        let slots=p.units[i].slots;if slots>size{continue}
        for capacity in (slots..=size).rev(){best[capacity]=best[capacity].max(best[capacity-slots]+weight(i));}
    }
    for group in 1..=p.groups.iter().copied().max().unwrap_or(0){
        let before=best;
        for &i in names.iter().filter(|i|p.groups[**i]==group){
            let slots=p.units[i].slots;if slots>size{continue}
            for capacity in slots..=size{best[capacity]=best[capacity].max(before[capacity-slots]+weight(i));}
        }
    }
    best[size]
}
fn globally_unreachable(p:&Planner,locked:&Names,excluded:&Names)->Result<Names,String>{
    // Optimistic, at-most-slot bounds use only global constraints, never a chosen
    // final roster. The monotone dependency closure adds each unit at most once.
    let all:Names=(0..p.units.len()).collect();let recipes=recipes(p,&all,excluded);
    let groups:BTreeMap<_,_>=locked.iter().filter(|i|p.groups[**i]!=0).map(|i|(p.groups[*i],*i)).collect();
    let mut pool=Names::new();
    for (i,c) in p.units.iter().enumerate(){if !excluded.contains(&i)&&p.eligible(c,p.options.size)?&&groups.get(&p.groups[i]).is_none_or(|j|*j==i){pool.insert(i);}}
    let mut possible:Names=pool.iter().copied().filter(|i|!p.units[*i].unlockable||p.confirmed(p.units[*i],p.options.size)).collect();
    loop{
        let mut additions=Names::new();
        for &i in pool.difference(&possible){
            let r=&recipes[&i];let own=p.groups[i];
            let helpers:Names=possible.iter().copied().filter(|j|*j!=i&&(own==0||p.groups[*j]!=own)).collect();
            let candidates:Names=r.candidates.iter().copied().filter(|j|helpers.contains(j)).collect();
            let stars=|j:usize|if p.options.stars.get(&p.units[j].name)==Some(&4)||p.options.early_availability.get(&p.units[j].name).is_some_and(|a|a.stars==4){4}else{3};
            let ready=match r.kind{
                "stars"|"distinct"|"active"|"holder"|"role"=>{
                    let star_count=r.kind=="stars"||r.kind=="active"&&r.traits.iter().any(|t|p.data.traits[p.trait_names[*t]].count_mode=="stars");
                    let contribution=|j|if star_count{stars(j)}else{1};
                    if r.kind=="distinct"{r.traits.iter().any(|t|maximum_setup(p,&candidates.iter().copied().filter(|j|p.unit_traits[*j].contains(t)).collect(),contribution)>=r.count)}
                    else{maximum_setup(p,&candidates,contribution)>=r.count}
                },
                "named"=>{
                    let named=p.helpers(&[i].into_iter().collect());
                    let groups:Vec<_>=named.iter().map(|j|p.groups[*j]).filter(|g|*g!=0).collect();
                    named.is_subset(&helpers)&&groups.iter().copied().collect::<BTreeSet<_>>().len()==groups.len()&&named.iter().map(|j|p.units[*j].slots).sum::<usize>()<=p.options.size
                },
                "costs"=>(1..=5).all(|cost|helpers.iter().any(|j|p.units[*j].cost==cost))&&maximum_setup(p,&candidates,|_|1)>=3,
                "many_traits"=>p.trait_names.iter().enumerate().filter(|(_,n)|!p.data.traits[**n].unique).filter(|(t,n)|{
                    let trait_=&p.data.traits[**n];
                    maximum_setup(p,&helpers,|j|if p.unit_traits[j].contains(t){if trait_.count_mode=="stars"{stars(j)}else{1}}else{0})>=trait_.breakpoints[0]
                }).count()>=r.count,
                _=>true,
            };
            if ready{additions.insert(i);}
        }
        if additions.is_empty(){break}possible.extend(additions);
    }
    Ok(pool.difference(&possible).copied().filter(|i|p.units[*i].unlockable).collect())
}

pub(crate) fn published_level(c: &Champion) -> Result<usize, String> {
    let published = if c.unlockable { c.unlock_level.as_str() } else { "Any" };
    if published == "Any" { return Ok(1) }
    published.strip_prefix("Level ").filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|s| s.parse::<usize>().ok()).ok_or_else(|| format!("{} has an invalid unlock level: {published:?}.", c.name))
}
fn minimum_level(c: &Champion) -> Result<usize, String> {
    let minimum = published_level(c)?;
    let gate = match c.cost { 1 => 1, 2 => 3, 3 => 4, 4 => 7, 5 => 9,
        7 if c.unlockable => match c.name.as_str() { "Sona" => 10, "Rakan" => 8, _ => 9 }, _ => 1 };
    Ok(minimum.max(gate))
}
fn eligible(c: &Champion, level: usize) -> Result<bool, String> {
    Ok(level >= minimum_level(c)? && (if c.cost == 7 { c.unlockable } else { (1..=5).contains(&c.cost) && SHOP_ODDS[level-1][c.cost-1] > 0 }))
}
fn unlock(c: &Champion) -> Value {
    let mut v = json!({"name":c.name,"condition":c.unlock_condition,"level":c.unlock_level,"source":c.unlock_source});
    if !c.unlock_note.is_empty() { v["note"] = json!(c.unlock_note); } v
}

#[derive(Clone)]
struct State {
    merit: f64, picks: [usize; 11], len: usize, counts: Vec<usize>, frontline: usize,
    collab: usize, bridge: bool, active: usize, anchor: f64, future: f64,
    td: bool, hype: bool, carry_flags: u8,
}
impl State {
    fn copy_from(&mut self,other:&Self){
        self.merit=other.merit;self.picks=other.picks;self.len=other.len;self.counts.clone_from(&other.counts);
        self.frontline=other.frontline;self.collab=other.collab;self.bridge=other.bridge;self.active=other.active;
        self.anchor=other.anchor;self.future=other.future;self.td=other.td;self.hype=other.hype;
        self.carry_flags=other.carry_flags;
    }
}
#[derive(Clone)]
struct Stage {
    stars: Vec<u8>, increments: Vec<Vec<(usize,usize)>>, units: Vec<f64>, anchors: Vec<f64>,
    td: Vec<bool>, hype: Vec<bool>, carry_flags: Vec<u8>, context: Vec<(usize, Vec<Vec<f64>>)>,
}
fn merge_pin_choices(pins:&mut BTreeMap<String,String>,selections:&Map<String,Value>)->Result<(),String>{
    for (slot,value) in selections{
        let name=value.as_str().ok_or("Each temporary choice must map canonical slot names to champion names.")?.to_owned();
        pins.retain(|old,chosen|old!=slot&&chosen!=slot&&chosen!=&name&&old!=&name);
        pins.insert(slot.clone(),name);
    }
    Ok(())
}
struct Planner<'a> {
    data: &'a GameData, options: &'a OptimizeOptions, units: Vec<&'a Champion>,
    ids: BTreeMap<String,usize>, trait_names: Vec<&'a str>, unit_traits: Vec<BTreeSet<usize>>,
    groups: Vec<usize>, collab: Names, front: Vec<usize>, partners: Vec<Names>, direct: Names,
    values: Vec<Vec<f64>>, tiers: Vec<Vec<usize>>, active: Vec<Vec<usize>>, progress: Vec<Vec<f64>>,
    stages: Vec<Stage>, jazz: Option<usize>, breadth: bool, named_helpers: Vec<Names>,
}
impl<'a> Planner<'a> {
    fn new(data: &'a GameData, options: &'a OptimizeOptions, breadth: bool) -> Result<Self,String> {
        Self::for_goal(data,options,breadth,&options.locked,&Map::new())
    }
    fn for_goal(data: &'a GameData, options: &'a OptimizeOptions, breadth: bool, goal:&[String],choices:&Map<String,Value>) -> Result<Self,String> {
        Self::for_goal_path(data,options,breadth,goal,choices,None)
    }
    fn for_goal_path(data: &'a GameData, options: &'a OptimizeOptions, breadth: bool, goal:&[String],choices:&Map<String,Value>,path_reroll:Option<bool>) -> Result<Self,String> {
        let keepers:BTreeSet<String>=goal.iter().cloned().collect();
        let reroll=options.mode!="cap_out"&&path_reroll.unwrap_or_else(||optimizer::reroll_plan(data,goal,&options.stars));
        let units: Vec<_> = data.champions.values().collect();
        let ids: BTreeMap<_,_> = units.iter().enumerate().map(|(i,c)|(c.name.clone(),i)).collect();
        let trait_names: Vec<_> = data.traits.keys().map(String::as_str).collect();
        let ti: BTreeMap<_,_> = trait_names.iter().enumerate().map(|(i,n)|(*n,i)).collect();
        let collab: Names = options.collab.iter().chain(std::iter::once(&"Sona".to_string())).filter_map(|n|ids.get(n).copied()).collect();
        let mut group_ids = BTreeMap::new();
        let mut groups = Vec::new();
        for c in &units {
            let group = if let Some(g) = &c.exclusive_group { let next = group_ids.len()+1; *group_ids.entry(g).or_insert(next) } else { 0 };
            groups.push(group);
        }
        let unit_traits: Vec<BTreeSet<usize>> = units.iter().enumerate().map(|(i,c)| {
            let mut names: BTreeSet<_> = c.traits.iter().filter(|n|n.as_str()!="Collab").filter_map(|n|ti.get(n.as_str()).copied()).collect();
            if collab.contains(&i) { if let Some(t) = ti.get("Collab") { names.insert(*t); } }
            if c.name == "Sona" && !options.sona_band.is_empty() { if let Some(t) = ti.get(options.sona_band.as_str()) { names.insert(*t); } }
            names
        }).collect();
        let front = units.iter().enumerate().map(|(i,c)|usize::from(is_tank(c) || unit_traits[i].iter().any(|t|optimizer::FRONTLINE_TRAITS.contains(&trait_names[*t])))).collect();
        let regular: BTreeSet<_> = data.traits.values().filter(|t|!t.unique && !["Jazz","Collab"].contains(&t.name.as_str())).map(|t|t.name.as_str()).collect();
        let native: Vec<BTreeSet<_>> = units.iter().map(|c|c.traits.iter().map(String::as_str).filter(|n|regular.contains(n)).collect()).collect();
        let jazz_units: Names = units.iter().enumerate().filter(|(_,c)|c.traits.iter().any(|t|t=="Jazz") || c.name=="Sona" && options.sona_band=="Jazz").map(|(i,_)|i).collect();
        let mut partners = vec![Names::new(); units.len()];
        for &j in &jazz_units { for &c in &collab { if j!=c && !native[j].is_disjoint(&native[c]) { partners[j].insert(c); partners[c].insert(j); } } }
        let direct = jazz_units.intersection(&collab).copied().collect();
        let without=&*WITHOUT;
        let helper_patterns:Vec<_>=units.iter().map(|c|Regex::new(&format!(r"(?:^|\W){}(?:$|\W)",regex::escape(&c.name))).expect("escaped champion name")).collect();
        let named_helpers=units.iter().enumerate().map(|(i,c)|{
            if !c.unlockable{return Names::new()}
            let positive=&c.unlock_condition[..without.find(&c.unlock_condition).map_or(c.unlock_condition.len(),|m|m.start())];
            helper_patterns.iter().enumerate().filter(|(j,pattern)|*j!=i&&pattern.is_match(positive)).map(|(j,_)|j).collect()
        }).collect();
        let mode = if breadth { "synergies" } else { &options.mode };
        let mut values=Vec::new(); let mut tiers=Vec::new(); let mut active=Vec::new(); let mut progress=Vec::new();
        // At most eleven four-star contributions; 44 is a count, not a breakpoint cap.
        for t in data.traits.values() {
            let mut vs=Vec::new(); let mut ts=Vec::new(); let mut av=Vec::new(); let mut ps=Vec::new();
            for count in 0..45 {
                let tier=t.breakpoints.partition_point(|p|*p<=count);
                let previous=if tier>0 {t.breakpoints[tier-1]} else {0};
                let following=t.breakpoints.get(tier).copied();
                let fraction=following.map_or(0.0,|n|(count-previous) as f64/(n-previous) as f64);
                let completion=if mode=="synergies" {8.0} else {0.5*(optimizer::trait_value(tier+1,mode)-optimizer::trait_value(tier,mode))};
                vs.push(if breadth {10000.0*boolean(tier>0 && !t.unique)} else if t.unique {boolean(tier>0)} else {optimizer::trait_value(tier,mode)});
                ts.push(tier); av.push(usize::from(tier>0 && !t.unique)); ps.push(if t.unique {0.0} else {completion*fraction});
            }
            values.push(vs); tiers.push(ts); active.push(av); progress.push(ps);
        }
        let mut stages=Vec::new();
        let mut pins=BTreeMap::<String,String>::new();let mut stage_keepers=keepers.clone();
        for level in 1..=11 {
            if let Some(selections)=choices.get(&level.to_string()).and_then(Value::as_object){
                merge_pin_choices(&mut pins,selections)?;
                stage_keepers.clone_from(&keepers);stage_keepers.extend(pins.values().cloned());
            }
            let mut stars_map=optimizer::planned_path_stars(data,level,&options.stars,&options.sona_band,&stage_keepers,reroll)?;
            for (name, assumption) in &options.early_availability {
                if level >= assumption.level {
                    let rank=stars_map.get_mut(name).expect("validated early champion");
                    *rank=(*rank).max(assumption.stars);
                }
            }
            let stars: Vec<_> = units.iter().map(|c|stars_map[&c.name]).collect();
            let increments=unit_traits.iter().enumerate().map(|(i,traits)|traits.iter().map(|&t|(t,if data.traits[trait_names[t]].count_mode=="stars" {stars[i] as usize} else {1})).collect()).collect();
            let units_values=units.iter().enumerate().map(|(i,c)|optimizer::unit_strategy_value(c,stars[i],level,mode)).collect();
            let anchors=units.iter().enumerate().map(|(i,c)|if ["balanced","high_value"].contains(&mode) {optimizer::frontline_anchor_value(c,stars[i],level)} else {0.0}).collect();
            let td=units.iter().enumerate().map(|(i,c)|unit_traits[i].iter().any(|t|trait_names[*t]=="True Damage") && c.name!="Senna" && ((c.name=="Qiyana" && stars[i]>=2)||(c.name=="Ekko" && stars[i]>=3)||(c.cost>=4 && stars[i]>=2))).collect();
            let hype=units.iter().enumerate().map(|(i,_)|stars[i]>=2 && unit_traits[i].iter().any(|t|trait_names[*t]=="Hyperpop")).collect();
            let carry_flags=units.iter().enumerate().map(|(i,c)|crate::guidance::carry_flags(c,stars[i])).collect();
            let mut context=Vec::new();
            for name in ["Gu Feng","True Damage","Hyperpop"] { if let Some(&t)=ti.get(name) { if !data.traits[name].unique {
                let tables=(0..2).map(|ready|(0..45).map(|count|optimizer::trait_context_adjustment(name,tiers[t][count],count,mode,level,ready!=0,ready!=0)).collect()).collect();
                context.push((t,tables));
            } } }
            stages.push(Stage{stars,increments,units:units_values,anchors,td,hype,carry_flags,context});
        }
        Ok(Self{data,options,units,ids,trait_names,unit_traits,groups,collab,front,partners,direct,values,tiers,active,progress,stages,jazz:ti.get("Jazz").copied(),breadth,named_helpers})
    }
    fn minimum_level(&self,c:&Champion)->Result<usize,String>{
        if let Some(level)=self.options.completed_unlocks.get(&c.name){
            let acquisition=if c.cost==7{published_level(c)?}else{(1..=11).find(|level|SHOP_ODDS[level-1][c.cost-1]>0).unwrap_or(11)};
            return Ok((*level).max(published_level(c)?).max(acquisition))
        }
        match self.options.early_availability.get(&c.name){
            Some(assumption)=>Ok(minimum_level(c)?.min(assumption.level).max(published_level(c)?)),
            None=>minimum_level(c),
        }
    }
    fn eligible(&self,c:&Champion,level:usize)->Result<bool,String>{
        if self.conflicting_confirmation(c,level){return Ok(false)}
        if self.confirmed(c,level){
            return Ok(level>=published_level(c)?&&(if c.cost==7{c.unlockable}else{(1..=5).contains(&c.cost)&&SHOP_ODDS[level-1][c.cost-1]>0}))
        }
        if self.options.early_availability.get(&c.name).is_some_and(|a|level>=a.level){
            return Ok(level>=published_level(c)?&&((1..=5).contains(&c.cost)||c.cost==7&&c.unlockable))
        }
        eligible(c,level)
    }
    fn confirmed(&self,c:&Champion,level:usize)->bool {
        self.options.completed_unlocks.get(&c.name).is_some_and(|at|level>=*at)
    }
    fn conflicting_confirmation(&self,c:&Champion,level:usize)->bool {
        c.exclusive_group.as_ref().is_some_and(|group|self.options.completed_unlocks.iter().any(|(name,at)|*at<=level&&name!=&c.name&&self.data.champions[name].exclusive_group.as_ref()==Some(group)))
    }
    fn actual(&self,level:usize)->bool {self.options.current_level>0&&level<=self.options.current_level}
    fn available(&self,c:&Champion,level:usize)->Result<bool,String> {
        Ok(self.eligible(c,level)?&&(!self.actual(level)||!c.unlockable||self.confirmed(c,level)))
    }
    fn confirmed_names(&self,level:usize)->Names {
        self.options.completed_unlocks.iter().filter(|(_,at)|**at<=level).map(|(name,_)|self.ids[name]).collect()
    }
    fn assumed(&self,level:usize)->Names{
        self.options.early_availability.iter().filter(|(_,a)|level>=a.level).map(|(name,_)|self.ids[name]).collect()
    }
    fn names(&self, names:&Names)->Vec<String> {names.iter().map(|i|self.units[*i].name.clone()).collect()}
    fn set(&self, names:&Value)->Names {names.as_array().into_iter().flatten().filter_map(|v|v.as_str()).filter_map(|n|self.ids.get(n).copied()).collect()}
    fn evaluate(&self,level:usize,names:&Names)->Result<Value,String> {
        let mut stars:BTreeMap<String,u8>=names.iter().map(|i|(self.units[*i].name.clone(),self.stages[level-1].stars[*i])).collect();
        let board=self.evaluate_stars(level,names,&stars)?;
        if level!=4 {return Ok(board)}
        let holders=guidance::progression_holders(&board,self.data);
        let mut promoted=false;
        for name in holders.as_object().into_iter().flatten().filter_map(|(_,name)|name.as_str()) {
            if self.data.champions[name].cost==1&&!self.options.stars.contains_key(name) {
                if let Some(rank)=stars.get_mut(name).filter(|rank|**rank<2) {*rank=2;promoted=true;}
            }
        }
        if promoted {self.evaluate_stars(level,names,&stars)} else {Ok(board)}
    }
    fn evaluate_stars(&self,level:usize,names:&Names,stars:&BTreeMap<String,u8>)->Result<Value,String>{
        optimizer::evaluate_team(self.data,&self.names(names),&self.options.collab,&self.options.mode,&self.options.sona_band,stars,Some(level))
    }
    fn helpers(&self,target:&Names)->Names {
        target.iter().flat_map(|i|self.named_helpers[*i].iter().copied()).collect()
    }
    fn dependencies(&self,target:&Names)->Names {
        let mut helpers=Names::new();let mut pending=target.clone();
        while !pending.is_empty() {let found:Names=self.helpers(&pending).difference(&helpers).copied().filter(|i|!target.contains(i)).collect();helpers.extend(&found);pending=found;} helpers
    }
    fn blocked(&self,locked:&Names)->Names {
        let mut bands:BTreeSet<&str>=self.options.collab.iter().flat_map(|n|self.data.champions[n].traits.iter().map(String::as_str)).collect();
        if !self.options.sona_band.is_empty(){bands.insert(&self.options.sona_band);}
        self.units.iter().enumerate().filter(|(i,c)| {
            if !c.unlockable || locked.contains(i) || self.confirmed(c,self.options.size) {return false}
            let required=if c.traits.iter().any(|n|n=="Hyperpop") {Some("Hyperpop")} else if c.traits.iter().any(|n|n=="Gu Feng") {Some("Gu Feng")} else {match c.name.as_str(){"Aphelios"=>Some("Heartsteel"),"Akali (K/DA)"=>Some("K/DA"),"Akali (True Damage)"=>Some("True Damage"),"Gwen"=>Some("Ionic Spark"),"Mordekaiser"=>Some("Pentakill"),"Taliyah"=>Some("Beach Pop"),_=>None}};
            required.is_some_and(|b|!bands.contains(b)) || c.name=="Baron Nashor" && !self.options.collab.iter().any(|n|n=="Baron Nashor"||n=="Malzahar") || c.name=="Rakan" && !self.options.collab.contains(&c.name)
        }).map(|(i,_)|i).collect()
    }
    fn search(&self,level:usize,pool:&[usize],weights:&[f64],prepare_sona:bool,previous:&Names,width:usize)->Option<Names> {
        // Buckets preserve retained count as a hard feasibility key. Exclusive groups
        // are a sorted eleven-element signature: no roster-sized masks or limits.
        let mut next=State{merit:0.0,picks:[0;11],len:0,counts:vec![0;self.trait_names.len()],frontline:0,collab:0,bridge:false,active:0,anchor:0.0,future:0.0,td:false,hype:false,carry_flags:0};
        type Key=([usize;11],usize);
        let mut buckets:Vec<BTreeMap<Key,Vec<State>>>=(0..=level).map(|_|BTreeMap::new()).collect();
        buckets[0].insert(([0;11],0),vec![State{merit:0.0,picks:[0;11],len:0,counts:vec![0;self.trait_names.len()],frontline:0,collab:0,bridge:false,active:0,anchor:0.0,future:0.0,td:false,hype:false,carry_flags:0}]);
        let stage=&self.stages[level-1];let mode=if self.breadth {"synergies"}else{self.options.mode.as_str()};
        for &i in pool {
            let c=self.units[i];if c.slots>level {continue}
            for filled in (0..=level-c.slots).rev() {
                let (left,right)=buckets.split_at_mut(filled+c.slots);
                let source=&left[filled];let destination=&mut right[0];
                for (&(groups,retained),states) in source {
                    let group=self.groups[i];if group!=0 && groups.contains(&group) {continue}
                    let mut next_groups=groups;if group!=0 {let hole=next_groups.iter().position(|g|*g==0).expect("at most eleven units");next_groups[hole]=group;next_groups.sort_unstable();}
                    let dest=destination.entry((next_groups,retained+usize::from(previous.contains(&i)))).or_default();
                    for old in states {
                        next.copy_from(old);next.picks[next.len]=i;next.len+=1;next.picks[..next.len].sort_unstable();
                        let mut gain=weights[i]+stage.units[i];
                        next.carry_flags|=stage.carry_flags[i];
                        gain+=optimizer::late_carry_value(next.carry_flags,level,mode)-optimizer::late_carry_value(old.carry_flags,level,mode);
                        for &(t,inc) in &stage.increments[i] {let count=old.counts[t];let nc=count+inc;next.counts[t]=nc;gain+=self.values[t][nc]-self.values[t][count];next.future+=self.progress[t][nc]-self.progress[t][count];next.active=next.active+self.active[t][nc]-self.active[t][count];}
                        next.td|=stage.td[i];next.hype|=stage.hype[i];
                        for (t,tables) in &stage.context {let (ready,nready)=match self.trait_names[*t]{"True Damage"=>(old.td,next.td),"Hyperpop"=>(old.hype,next.hype),_=>(false,false)};gain+=tables[usize::from(nready)][next.counts[*t]]-tables[usize::from(ready)][old.counts[*t]];}
                        next.anchor=next.anchor.max(stage.anchors[i]);gain+=next.anchor-old.anchor;
                        next.bridge|=self.direct.contains(&i)||old.picks[..old.len].iter().any(|j|self.partners[i].contains(j));
                        let jt=self.jazz.map_or(0,|t|self.tiers[t][old.counts[t]]);let njt=self.jazz.map_or(0,|t|self.tiers[t][next.counts[t]]);
                        gain+=optimizer::jazz_bonus(next.active,njt,next.bridge,mode)-optimizer::jazz_bonus(old.active,jt,old.bridge,mode);
                        next.frontline+=self.front[i];next.collab+=usize::from(self.collab.contains(&i));
                        if ["balanced","high_value"].contains(&mode) {gain+=30.0*(next.frontline.min(2)-old.frontline.min(2)) as f64+20.0*(i32::from(next.len>next.frontline)-i32::from(old.len>old.frontline)) as f64;}
                        if mode=="high_value" {gain+=30.0*c.cost as f64;}
                        if prepare_sona {gain+=100.0*(next.collab.min(3)-old.collab.min(3)) as f64+200.0*boolean(old.collab<3&&next.collab>=3);}
                        next.merit+=gain;
                        let partial=filled+c.slots<level;
                        let position=dest.partition_point(|s|compare_state(s,&next,partial)!=Ordering::Greater);
                        if position<width {
                            if dest.len()==width {let recycled=dest.pop().unwrap();let accepted=std::mem::replace(&mut next,recycled);dest.insert(position,accepted);}
                            else {dest.insert(position,next.clone());}
                        }
                    }
                }
            }
        }
        let feasible=&buckets[level];let max_retained=feasible.keys().map(|(_,r)|*r).max()?;
        let minimum=previous.len().saturating_sub(if level>=8{2}else{1}).min(max_retained);
        let winner=feasible.iter().filter(|((_,r),_)|*r>=minimum).flat_map(|(_,s)|s).min_by(|a,b|compare_state(a,b,false))?;
        Some(winner.picks[..winner.len].iter().copied().collect())
    }
    fn board(&self,level:usize,eligible:&Names,target:&Names,previous:&Names,helpers:&Names,prepare_sona:bool)->Result<Option<Value>,String> {
        let target_traits:BTreeSet<_>=target.iter().flat_map(|i|self.unit_traits[*i].iter().copied()).collect();
        let goals:BTreeMap<_,_>=target.iter().filter(|i|self.groups[**i]!=0).map(|i|(self.groups[*i],*i)).collect();
        let mut ranked:Vec<_>=eligible.iter().copied().filter(|i|self.groups[*i]==0||goals.get(&self.groups[*i]).is_none_or(|j|*j==*i)).collect();
        let mut weights=vec![0.0;self.units.len()];
        let assumed=self.assumed(level);
        for &i in &ranked {let c=self.units[i];let odds=if c.cost<=5{SHOP_ODDS[level-1][c.cost-1]}else{100};weights[i]=600.0*boolean(target.contains(&i))+450.0*boolean(previous.contains(&i))+850.0*boolean(helpers.contains(&i))+5000.0*boolean(assumed.contains(&i))+12.0*self.unit_traits[i].intersection(&target_traits).count() as f64+(if prepare_sona&&self.collab.contains(&i){85.0}else{0.0})-9.0*c.cost as f64-15.0*10usize.saturating_sub(odds) as f64-25.0*boolean(c.unlockable);}
        ranked.sort_by(|a,b|weights[*b].total_cmp(&weights[*a]).then(a.cmp(b)));
        let core:Names=target.union(previous).copied().chain(helpers.iter().copied()).chain(assumed).chain(self.collab.iter().copied().filter(|_|prepare_sona)).collect();
        let mut pool:Vec<_>=ranked.iter().copied().filter(|i|core.contains(i)).collect();pool.extend(ranked.iter().copied().filter(|i|!core.contains(i)).take(14));
        let mut answer=self.search(level,&pool,&weights,prepare_sona,previous,8);
        if pool.len()<ranked.len() && answer.as_ref().is_none_or(|a|previous.difference(a).count()>if level>=8{2}else{1}) {answer=self.search(level,&ranked,&weights,prepare_sona,previous,8);}
        answer.map(|a|self.evaluate(level,&a)).transpose()
    }
    fn pinned_board(&self,level:usize,pool:&Names,target:&Names,previous:&Names,pins:&BTreeMap<String,String>)->Result<Option<Value>,String>{
        let prepare_sona=target.iter().any(|i|self.units[*i].name=="Sona"&&!self.confirmed(self.units[*i],level));
        if pins.is_empty(){return self.board(level,pool,target,previous,&Names::new(),prepare_sona)}
        let required:Names=pins.values().map(|name|self.ids[name]).collect();
        let forbidden:Names=pins.iter().filter(|(slot,name)|slot!=name&&!pins.values().any(|v|v==*slot)).map(|(slot,_)|self.ids[slot]).collect();
        if !required.is_subset(pool){return Err(format!("Pinned slot conflicts with unlock or source availability at level {level}; unpin it before changing this path."))}
        let candidates:Vec<_>=pool.difference(&forbidden).copied().collect();
        let weights:Vec<_>=(0..self.units.len()).map(|i|100000.0*boolean(required.contains(&i))+600.0*boolean(target.contains(&i))+450.0*boolean(previous.contains(&i))).collect();
        let picks=self.search(level,&candidates,&weights,prepare_sona,previous,8).filter(|names|required.is_subset(names))
            .ok_or_else(||format!("Pinned slots cannot fit the required exact-slot board at level {level}; unpin a conflicting choice."))?;
        Ok(Some(self.evaluate(level,&picks)?))
    }
}
fn compare_state(a:&State,b:&State,partial:bool)->Ordering {
    let score=|s:&State|s.merit+if partial{s.future}else{0.0};
    score(b).total_cmp(&score(a)).then_with(||a.picks[..a.len].cmp(&b.picks[..b.len]))
}

#[derive(Clone,Debug)]
struct Recipe {kind:&'static str,candidates:Vec<usize>,count:usize,traits:Vec<usize>,requirements:String,accumulating:bool,distinct:bool}
fn recipes(p:&Planner,target:&Names,excluded:&Names)->BTreeMap<usize,Recipe> {
    let distinct=&*DISTINCT;
    let stars=&*STAR_LEVELS;
    let holder=&*ITEM_HOLDER;
    let separators=&*HOLDER_SEPARATOR;
    let mut result=BTreeMap::new();
    for &i in target {let c=p.units[i];if !c.unlockable {continue}
        let mut r=Recipe{kind:"manual",candidates:vec![],count:0,traits:vec![],requirements:c.unlock_condition.clone(),accumulating:false,distinct:false};
        let active=match c.name.as_str(){"Thresh"=>Some("Classical"),"Lulu"|"Tristana"|"Teemo"=>Some("Hyperpop"),"Vayne"=>Some("Rock"),"Akali (True Damage)"=>Some("True Damage"),"Gwen"=>Some("Ionic Spark"),"Sona"=>Some("Collab"),_=>None};
        let mut bands=Vec::new();
        if let Some(m)=distinct.captures(&c.unlock_condition).filter(|_|!c.unlock_condition.contains("star levels of")) {r.kind="distinct";r.count=m[1].parse().unwrap();r.distinct=true;bands=m[2].split(" or ").map(str::to_owned).collect();}
        else if let Some(band)=active {r.kind="active";r.accumulating=true;r.count=p.data.traits.get(band).map_or(2,|t|t.breakpoints[0]);bands.push(band.to_owned());}
        else if c.unlock_condition.contains("star levels of") {if let Some(m)=stars.captures(&c.unlock_condition){r.kind="stars";r.count=m[1].parse().unwrap();bands=STAR_SEPARATOR.split(&m[2]).map(str::to_owned).collect();}}
        else if c.unlock_condition.contains("holding") && p.helpers(&[i].into_iter().collect()).is_empty() {if let Some(m)=holder.captures(&c.unlock_condition){r.kind="holder";r.count=1;bands=separators.split(&m[1]).map(str::to_owned).collect();}}
        else {match c.name.as_str(){"Alistar"=>{r.kind="role";r.count=1},"Vladimir"=>{r.kind="position";r.count=1},"Smeech"=>{r.kind="many_traits";r.count=8},"Taliyah"=>{r.kind="costs";r.count=5;bands.push("Beach Pop".into())},_=>{}}}
        let named=p.helpers(&[i].into_iter().collect());
        if r.kind=="manual"&&!named.is_empty(){r.kind="named";r.count=1;r.candidates=named.into_iter().collect();r.accumulating=["damage","interest"].iter().any(|s|c.unlock_condition.contains(s));}
        r.traits=bands.iter().filter_map(|b|p.trait_names.iter().position(|t|t==b)).collect();
        if !bands.is_empty(){r.candidates=p.units.iter().enumerate().filter(|(j,_)|*j!=i&&!excluded.contains(j)&&r.traits.iter().any(|t|p.unit_traits[*j].contains(t))).map(|(j,_)|j).collect();}
        else if r.kind=="role" {r.candidates=p.units.iter().enumerate().filter(|(_,u)|!u.unlockable&&["caster","marksman"].iter().any(|role|u.role.to_lowercase().contains(role))).map(|(j,_)|j).collect();}
        r.candidates.retain(|j|!excluded.contains(j));r.candidates.sort_by_key(|j|(p.units[*j].unlockable,p.minimum_level(p.units[*j]).unwrap_or(usize::MAX),p.units[*j].cost,*j));
        result.insert(i,r);
    } result
}

fn scheduled_setup_helpers(p:&Planner,level:usize,i:usize,r:&Recipe,board:&Value)->Option<Names>{
    if board.is_null(){return None}
    let names=p.set(&board["champions"]);
    if names.contains(&i){return None}
    let candidates:Names=r.candidates.iter().copied().filter(|j|names.contains(j)).collect();
    let ready=match r.kind{
        "many_traits"=>board["active"].as_u64().unwrap_or(0)>=r.count as u64,
        "distinct"=>r.traits.iter().any(|t|candidates.iter().filter(|j|p.unit_traits[**j].contains(t)).count()>=r.count),
        "active"=>board["traits"].as_array().into_iter().flatten().any(|row|r.traits.iter().any(|t|row["name"]==p.trait_names[*t])&&row["tier"].as_u64().unwrap_or(0)>0),
        "stars"=>candidates.iter().map(|j|board["planned_stars"][&p.units[*j].name].as_u64().unwrap_or(1) as usize).sum::<usize>()>=r.count,
        "named"=>{
            let required=p.helpers(&[i].into_iter().collect());
            required.is_subset(&names)&&required.iter().all(|j|{
                let pattern=Regex::new(&format!(r"(\d+)-star {}(?:\W|$)",regex::escape(&p.units[*j].name))).unwrap();
                pattern.captures(&r.requirements).is_none_or(|m|board["planned_stars"][&p.units[*j].name].as_u64().unwrap_or(1)>=m[1].parse::<u64>().unwrap())
            })
        },
        "holder"|"role"=>candidates.len()>=r.count,
        "costs"=>(1..=5).all(|cost|names.iter().any(|j|p.units[*j].cost==cost))&&candidates.len()>=3,
        "position"=>level>=published_level(p.units[i]).ok()?&&board["positioning_constraint"]=="one_front_two_rows",
        _=>false,
    };
    if !ready{return None}
    Some(match r.kind{
        "many_traits"|"position"|"costs"=>names,
        "named"=>p.helpers(&[i].into_iter().collect()),
        _=>candidates,
    })
}
fn unavailable(mut item:Value,note:&str)->Value {item["status"]=json!("unavailable");let s=item["note"].as_str().unwrap().to_owned()+note;item["note"]=json!(s);item}
fn preparation(p:&Planner,breadth:&Planner,level:usize,i:usize,r:&Recipe,target:&Names,excluded:&Names,preferred:&Names,pinned:&Names)->Result<Value,String> {
    let c=p.units[i];let mut item=unlock(c);
    let note=format!("Complete and confirm the sourced task before buying this target. No progress, items, stars, gold or combat outcomes are assumed.{}",if c.unlock_note.is_empty(){String::new()}else{format!(" {}",c.unlock_note)});
    let object=item.as_object_mut().unwrap();object.extend(json!({"target":c.name,"board":null,"requirements":r.requirements,"kind":r.kind,"accumulating":r.accumulating,"status":"manual","note":note,"planning_level":p.minimum_level(c)?,"published_unlock_level":c.unlock_level}).as_object().unwrap().clone());
    if p.confirmed(c,level){
        item["status"]=json!("completed");item["completed_level"]=json!(p.options.completed_unlocks[&c.name]);
        item["note"]=json!("User-confirmed sourced task completion at the recorded level. This does not assert purchased copies, stars, items, gold or cashout.");
        return Ok(item)
    }
    if r.kind=="manual" {item["note"]=json!(format!("{} This is a manual economy, streak, reroll or ensemble task; a lineup alone cannot satisfy it.",item["note"].as_str().unwrap()));return Ok(item)}
    if r.kind=="position" {if let Some(minimum)=c.unlock_level.strip_prefix("Level ").and_then(|v|v.parse::<usize>().ok()){if level<minimum{return Ok(unavailable(item," The positioning unlock check is unavailable before its published player-level requirement."))}}}
    let allowed:Names=r.candidates.iter().copied().chain(p.dependencies(&[i].into_iter().collect())).collect();
    let goal_groups:BTreeMap<_,_>=target.iter().filter(|j|p.groups[**j]!=0).map(|j|(p.groups[*j],*j)).collect();
    let mut pool=Vec::new();for (j,u) in p.units.iter().enumerate(){if j!=i&&!excluded.contains(&j)&&p.available(u,level)?&&(!u.unlockable||allowed.contains(&j)||p.confirmed(u,level))&&(p.groups[j]==0||goal_groups.get(&p.groups[j]).is_none_or(|g|*g==j))&&!pinned.iter().any(|k|*k!=j&&p.groups[j]!=0&&p.groups[j]==p.groups[*k]){pool.push(j)}}
    let eligible_set:Names=pool.iter().copied().collect();
    if !pinned.is_subset(&eligible_set){return Ok(unavailable(item," The selected champions cannot be retained on this setup at this level."))}
    let mut candidates:Vec<_>=r.candidates.iter().copied().filter(|j|eligible_set.contains(j)).collect();candidates.sort_by_key(|j|(!pinned.contains(j),!preferred.contains(j),r.candidates.iter().position(|k|k==j).unwrap()));
    let mut required=Vec::<usize>::new();
    match r.kind {
        "distinct"|"active"=>{if r.kind=="distinct"&&r.traits.len()>1 {candidates=r.traits.iter().map(|t|candidates.iter().copied().filter(|j|p.unit_traits[*j].contains(t)).collect::<Vec<_>>()).find(|g|g.len()>=r.count).unwrap_or_default();}required.extend(candidates.iter().copied().take(r.count));if required.len()<r.count{return Ok(unavailable(item," Not enough eligible distinct setup champions at this level under the exclusions and shop gates."))}}
        "named"|"holder"|"role"|"stars"=>{
            if r.kind=="stars"{let mut slots=pinned.iter().map(|j|p.units[*j].slots).sum::<usize>();let mut total=0;let mut groups:BTreeSet<_>=pinned.iter().map(|j|p.groups[*j]).filter(|g|*g!=0).collect();for &j in &candidates{let group=p.groups[j];if pinned.contains(&j)||slots+p.units[j].slots<=level&&(group==0||!groups.contains(&group)){required.push(j);if !pinned.contains(&j){slots+=p.units[j].slots;}total+=p.stages[level-1].stars[j] as usize;if group!=0{groups.insert(group);}if total>=r.count{break}}}}
            else{required.extend(candidates.iter().copied().take(if r.kind=="named"{candidates.len()}else{1}));}
            if r.kind=="named"&&required.iter().copied().collect::<Names>()!=p.helpers(&[i].into_iter().collect()){return Ok(unavailable(item," A named prerequisite is excluded or unavailable."))}
            if required.is_empty(){return Ok(unavailable(item," No eligible sourced setup holder/helper is available."))}
        }
        "costs"=>{for cost in 1..=5 {let best=pool.iter().copied().filter(|j|p.units[*j].cost==cost&&!p.units[*j].unlockable).min_by_key(|j|(!p.units[*j].traits.iter().any(|t|t=="Beach Pop"),!target.contains(j),*j));if let Some(j)=best{required.push(j);}else{return Ok(unavailable(item," A champion of each ordinary cost is not yet eligible."))}}
            for &j in &candidates {if required.iter().filter(|k|p.units[**k].traits.iter().any(|t|t=="Beach Pop")).count()>=3{break}if !required.contains(&j){required.push(j);}}
            item["positioning_requirement"]=json!("Place a champion of each cost in an actual Beach Pop hex. Instruments/hex state must be checked in game; generic placement is not proof.");}
        _=>{}
    }
    for &j in pinned{if !required.contains(&j){required.push(j);}}
    if required.iter().map(|j|p.units[*j].slots).sum::<usize>()>level{return Ok(unavailable(item," The sourced setup needs more board slots."))}
    pool.retain(|j|!p.units[*j].unlockable||required.contains(j)||p.confirmed(p.units[*j],level));
    let mut weights=vec![0.0;p.units.len()];for &j in &pool {weights[j]=if required.contains(&j){100000.0}else{200.0*boolean(preferred.contains(&j))+20.0*boolean(target.contains(&j))-p.units[j].cost as f64};}
    let searcher=if r.kind=="many_traits"{breadth}else{p};
    let stable=if r.kind=="many_traits"&&!preferred.is_empty(){
        p.board(level,&pool.iter().copied().collect(),target,preferred,preferred,false)?
            .filter(|board|board["active"].as_u64().unwrap_or(0)>=r.count as u64&&pinned.is_subset(&p.set(&board["champions"]))).map(|board|p.set(&board["champions"]))
    }else{None};
    let picks=stable.or_else(||searcher.search(level,&pool,&weights,false,&Names::new(),if r.kind=="many_traits"{32}else{8}));
    let Some(picks)=picks.filter(|picks|required.iter().all(|i|picks.contains(i))) else{return Ok(unavailable(item," No exact-slot setup satisfies these constraints."))};
    let ranks:BTreeMap<String,u8>=picks.iter().map(|j|(p.units[*j].name.clone(),p.stages[level-1].stars[*j])).collect();
    if r.kind=="stars"&&candidates.iter().filter(|j|picks.contains(j)).map(|j|ranks[&p.units[*j].name] as usize).sum::<usize>()<r.count{
        return Ok(unavailable(item," The normal stage/reroll ranks cannot yet meet this star total; wait for the scheduled upgrades or explicitly request ranks. No automatic three/four-star setup is invented."))
    }
    if r.kind=="named"{for &j in &required{
        let pattern=Regex::new(&format!(r"(\d+)-star {}(?:\W|$)",regex::escape(&p.units[j].name))).unwrap();
        if let Some(m)=pattern.captures(&r.requirements){
            if ranks[&p.units[j].name]<m[1].parse::<u8>().unwrap(){
                return Ok(unavailable(item," A named helper has not reached its required star rank in the normal stage schedule; earn a later upgrade or explicitly plan that rank."))
            }
        }
    }}
    let mut board=p.evaluate_stars(level,&picks,&ranks)?;
    if r.kind=="many_traits"&&board["active"].as_u64().unwrap_or(0)<r.count as u64 {let note=format!(" Search found only {} active non-unique traits, not the required {}; acquisition is not ready.",board["active"],r.count);return Ok(unavailable(item,&note))}
    if r.kind=="active"&&!board["traits"].as_array().unwrap().iter().any(|row|r.traits.iter().any(|t|row["name"]==p.trait_names[*t])&&row["tier"].as_u64().unwrap_or(0)>0){return Ok(unavailable(item," Required band is not active."))}
    if r.kind=="position" {board["positioning_constraint"]=json!("one_front_two_rows");item["positioning_requirement"]=json!("Exactly ONE champion in rows 0–1 (front two rows); every other champion in rows 2–3. Hold this setup for the actual unlock check; default tank positioning is not valid for this task.");}
    let mut note=item["note"].as_str().unwrap().to_owned();
    if r.kind=="stars" {note.push_str(&format!(" Plan and earn the displayed {}-star setup before checking the unlock; planned copies are not owned copies.",r.count));}
    if r.accumulating {note.push_str(" Hold/revisit this active setup across combats until the displayed accumulated requirement is actually earned; changing levels does not finish it.");}
    item["note"]=json!(note);item["board"]=board;item["status"]=json!("conditional_setup");item["helpers"]=json!(required.iter().map(|i|&p.units[*i].name).collect::<Vec<_>>());item["conditional_dependencies"]=json!(picks.iter().filter(|j|p.units[**j].unlockable).map(|j|unlock(p.units[*j])).collect::<Vec<_>>());Ok(item)
}

fn temporary_selections(p:&Planner,level:usize,board:Option<Value>,fallback:Option<&Value>,target:&Names,previous:&Names,excluded:&Names,blocked:&Names,dependencies:&Names,protected:&BTreeMap<usize,Names>,slots:&[String],choices:&Map<String,Value>,pins:&BTreeMap<String,String>)->Result<(Option<Value>,Vec<Value>),String> {
    let Some(board)=board else {if !choices.is_empty(){return Err(format!("No temporary choices are available at level {level}."))}return Ok((None,vec![]))};
    let mut current=p.set(&board["champions"]);let mut assigned=Names::new();let mut validated=BTreeSet::new();let mut options=Vec::new();
    let fallback_names=fallback.map(|b|p.set(&b["champions"])).unwrap_or_default();
    let pending_recipes=recipes(p,target,excluded);
    let minimum_active=pending_recipes.iter().filter(|(i,r)|r.kind=="many_traits"&&!p.confirmed(p.units[**i],level)&&board["active"].as_u64().unwrap_or(0)>=r.count as u64).map(|(_,r)|r.count).max().unwrap_or(0);
    let final_traits:BTreeSet<_>=target.iter().flat_map(|i|p.unit_traits[*i].iter().copied()).collect();
    for slot in slots {
        let &original=p.ids.get(slot).ok_or_else(||format!("Unknown temporary slot {slot:?} at level {level}."))?;
        let occupants:Names=current.iter().copied().filter(|i|!assigned.contains(i)).collect();
        if occupants.is_empty(){if choices.contains_key(slot){return Err(format!("Temporary slot {slot:?} is unavailable at level {level}."))}continue}
        let priority=|i:usize|guidance::replacement_priority(&[slot.clone()],&[p.units[i].name.clone()],p.data);
        let selected=if let Some(i)=pins.get(slot).and_then(|name|p.ids.get(name)).filter(|i|occupants.contains(*i)){*i}else if occupants.contains(&original){original}else{*occupants.iter().min_by_key(|i|(-priority(**i),**i)).unwrap()};
        let final_core=target.contains(&selected);
        let base:Names=current.iter().copied().filter(|i|*i!=selected).collect();
        let mut valid=vec![selected];
        if !final_core {for (i,c) in p.units.iter().enumerate(){
            if current.contains(&i)||excluded.contains(&i)||c.unlockable&&!p.confirmed(c,level)||blocked.contains(&i)&&!dependencies.contains(&i)||!p.available(c,level)?||c.slots!=p.units[selected].slots {continue}
            if protected.get(&selected).is_some_and(|allowed|!allowed.contains(&i)){continue}
            if p.groups[i]!=0&&base.iter().any(|j|p.groups[*j]==p.groups[i]){continue}
            let removed=previous.iter().filter(|j|!base.contains(*j)&&**j!=i).count();
            if removed>(if level>=8{2usize}else{1usize}).max(previous.difference(&current).count()){continue}
            let profile=guidance::item_profile(p.units[original]);
            if !(profile!="unknown"&&guidance::item_profile(c)==profile)&&p.unit_traits[i].is_disjoint(&p.unit_traits[original]){continue}
            if minimum_active>0{
                let mut candidate=base.clone();candidate.insert(i);
                if p.evaluate(level,&candidate)?["active"].as_u64().unwrap_or(0)<minimum_active as u64{continue}
            }
            valid.push(i);
        }}
        valid[1..].sort_by_key(|i|(!fallback_names.contains(i),-priority(*i),std::cmp::Reverse(p.unit_traits[*i].intersection(&p.unit_traits[original]).count()),p.units[*i].cost,*i));
        let chosen=if let Some(value)=choices.get(slot){let name=value.as_str().ok_or("Each temporary choice must map canonical slot names to champion names.")?;*p.ids.get(name).ok_or_else(||format!("Invalid temporary choice {name:?} for {slot:?} at level {level}."))?}else{selected};
        if final_core&&chosen!=selected{return Err(format!("Cannot replace {} at level {level}: fielded members of the selected Final roster are protected. Choose a nonfinal temporary slot instead.",p.units[selected].name))}
        if !valid.contains(&chosen){return Err(format!("Invalid temporary choice {:?} for {slot:?} at level {level}: source access, exclusions, exclusive variants, exact slots or required setup prevent this replacement. Unpin this slot or choose an offered alternative.",p.units[chosen].name))}
        current=base;current.insert(chosen);assigned.insert(chosen);
        validated.insert(slot.as_str());
        // Keep canonical identity and validate explicit history even when the
        // accepted choice joins Final and no longer exposes a temporary control.
        if target.contains(&chosen){continue}
        let stars:BTreeMap<_,_>=valid.iter().map(|i|(p.units[*i].name.clone(),p.stages[level-1].stars[*i])).collect();
        let mut routes=Map::new();let mut recommended=selected;let mut best=None;
        for &i in &valid{
            let traits:Vec<_>=p.unit_traits[i].intersection(&final_traits).map(|t|p.trait_names[*t]).collect();
            let branch_traits:Vec<_>=p.units[i].traits.iter().filter(|name|p.data.traits.get(*name).is_some_and(|trait_|!trait_.unique)).collect();
            let targets:Vec<_>=pending_recipes.iter().filter(|(target,recipe)|!p.confirmed(p.units[**target],level)
                &&(recipe.candidates.contains(&i)||p.dependencies(&[**target].into_iter().collect()).contains(&i))).map(|(target,_)|p.units[*target].name.as_str()).collect();
            let rank=(targets.len(),usize::from(target.contains(&i)),traits.len(),p.stages[level-1].stars[i],std::cmp::Reverse(i));
            if best.is_none_or(|old|rank>old){best=Some(rank);recommended=i;}
            routes.insert(p.units[i].name.clone(),json!({"final_traits":traits,"branch_traits":branch_traits,"unlock_targets":targets}));
        }
        options.push(json!({"slot":slot,"selected":p.units[chosen].name,"options":valid.iter().map(|i|&p.units[*i].name).collect::<Vec<_>>(),"planned_stars":stars,"recommended":p.units[recommended].name,"routes":routes}));
    }
    for slot in choices.keys(){if !validated.contains(slot.as_str()){return Err(format!("Unknown temporary slot {slot:?} at level {level}."))}}
    Ok((Some(p.evaluate(level,&current)?),options))
}

fn subsets(names:&[usize],count:usize)->Vec<Vec<usize>> {
    fn visit(names:&[usize],count:usize,start:usize,current:&mut Vec<usize>,out:&mut Vec<Vec<usize>>){
        if current.len()==count {out.push(current.clone());return}
        let remaining=count-current.len();if names.len().saturating_sub(start)<remaining{return}
        for i in start..=names.len()-remaining {current.push(names[i]);visit(names,count,i+1,current,out);current.pop();}
    }
    let mut out=Vec::new();visit(names,count,0,&mut Vec::new(),&mut out);out
}
fn upgrade_phases(p:&Planner,level:usize,arrival:Option<&Value>,goal:&Value)->Result<Vec<Value>,String>{
    let Some(arrival)=arrival else{return Ok(vec![])};
    let mut current=p.set(&arrival["champions"]);let target=p.set(&goal["champions"]);let mut phases=Vec::new();
    while current!=target {
        let outgoing:Vec<_>=current.difference(&target).copied().collect();let incoming:Vec<_>=target.difference(&current).copied().collect();
        let mut exchange=None;
        for count in 1..=outgoing.len(){
            let mut best:Option<(i64,usize,Vec<usize>,Vec<usize>)>=None;
            for removed in subsets(&outgoing,count){
                let budget:usize=removed.iter().map(|i|p.units[*i].slots).sum();
                for added_count in 1..=incoming.len(){for added in subsets(&incoming,added_count){
                    if added.iter().map(|i|p.units[*i].slots).sum::<usize>()!=budget{continue}
                    let removed_names:Vec<_>=removed.iter().map(|i|p.units[*i].name.clone()).collect();let added_names:Vec<_>=added.iter().map(|i|p.units[*i].name.clone()).collect();
                    let candidate=(-guidance::replacement_priority(&removed_names,&added_names,p.data),added.len(),removed.clone(),added);
                    if best.as_ref().is_none_or(|b|candidate<*b){best=Some(candidate)}
                }}
            }
            if let Some((_,_,removed,added))=best{exchange=Some((removed,added));break}
        }
        let (removed,added)=exchange.ok_or("Final upgrade path has inconsistent exact-slot goals.")?;
        let mut following=current.clone();for i in &removed{following.remove(i);}following.extend(&added);
        let board=p.evaluate(level,&following)?;
        let mut milestones=vec!["Buy the listed additions before selling the outgoing units; hold the arrival board until the required hits and unlock tasks are complete. No owned copies, bench space or gold are assumed.".to_owned()];
        if removed.len()>2||added.len()>2{milestones.push("These weighted slot sizes require a larger simultaneous exchange; no empty-slot or invented filler board is shown.".into());}
        if removed.iter().chain(&added).any(|i|p.units[*i].slots==2){milestones.push("Baron / weighted two-slot exchange: secure all incoming units before freeing both required slots.".into());}
        phases.push(json!({"stars":board["planned_stars"],"board":board,"added":added.iter().map(|i|&p.units[*i].name).collect::<Vec<_>>(),"removed":removed.iter().map(|i|&p.units[*i].name).collect::<Vec<_>>(),"retained":p.names(&current.intersection(&following).copied().collect()),"reason":"Conditional final-level upgrade, not an instant leveling pivot. Buy before selling; replace setup helpers only after the relevant unlock is complete.","unlocks":following.iter().filter(|i|p.units[**i].unlockable).map(|i|unlock(p.units[*i])).collect::<Vec<_>>(),"milestones":milestones}));
        current=following;
    }
    Ok(phases)
}

struct OrderedPath<'p,'a> {
    planner:&'p Planner<'a>, breadth:&'p Planner<'a>, level:usize,
    target:&'p Names, excluded:&'p Names, blocked:&'p Names, preferred:Names,
    recipes:&'p BTreeMap<usize,Recipe>, approved:Names,
    current:Option<Value>, phases:Vec<Value>, pinned:Names,
}
impl OrderedPath<'_,'_> {
    fn phase(&self,board:Value,star_upgrades:Vec<Value>,unlock_checks:Vec<Value>,reason:&str)->Value{
        let p=self.planner;let names=p.set(&board["champions"]);
        json!({"stars":board["planned_stars"],"board":board,"added":[],"removed":[],"retained":p.names(&names),
            "star_upgrades":star_upgrades,"unlock_checks":unlock_checks,
            "unlocks":names.iter().filter(|i|p.units[**i].unlockable).map(|i|unlock(p.units[*i])).collect::<Vec<_>>(),
            "milestones":[reason],"reason":reason})
    }
    fn move_to(&mut self,goal:&Value)->Result<(),String>{
        let p=self.planner;let target=p.set(&goal["champions"]);
        if self.current.is_none(){
            let ranks=target.iter().map(|i|(p.units[*i].name.clone(),1)).collect();
            let board=p.evaluate_stars(self.level,&target,&ranks)?;
            let mut phase=self.phase(board.clone(),vec![],vec![],"Buy the conditionally unlocked additions before fielding; no copies or gold are assumed.");
            phase["added"]=json!(p.names(&target));self.phases.push(phase);self.current=Some(board);
        }
        let current=self.current.as_ref().unwrap();
        let ranks:BTreeMap<String,u8>=target.iter().map(|i|{let name=&p.units[*i].name;(name.clone(),current["planned_stars"][name].as_u64().unwrap_or(1) as u8)}).collect();
        let roster_goal=p.evaluate_stars(self.level,&target,&ranks)?;
        for mut phase in upgrade_phases(p,self.level,self.current.as_ref(),&roster_goal)?{
            let names=p.set(&phase["board"]["champions"]);
            let ranks=names.iter().map(|i|{let name=&p.units[*i].name;(name.clone(),self.current.as_ref().unwrap()["planned_stars"][name].as_u64().unwrap_or(1) as u8)}).collect();
            let board=p.evaluate_stars(self.level,&names,&ranks)?;
            phase["stars"]=board["planned_stars"].clone();phase["board"]=board.clone();
            phase["star_upgrades"]=json!([]);phase["unlock_checks"]=json!([]);
            if self.level==p.options.size&&target==*self.target{
                phase["final_pivot"]=json!(true);
                phase["reason"]=json!("Explicit fixed-final-roster pivot: buy the incoming final units before selling outgoing temporary choices or setup helpers. Earlier slot pins end only at this declared transition.");
            }
            self.phases.push(phase);self.current=Some(board);
        }
        let current=self.current.as_ref().unwrap();let mut ranks=BTreeMap::new();let mut upgrades=Vec::new();
        for &i in &target{let name=&p.units[i].name;let from=current["planned_stars"][name].as_u64().unwrap_or(1) as u8;let to=goal["planned_stars"][name].as_u64().unwrap_or(1) as u8;
            if to>from{upgrades.push(json!({"champion":name,"from":from,"to":to}));}ranks.insert(name.clone(),from.max(to));}
        if !upgrades.is_empty(){let board=p.evaluate_stars(self.level,&target,&ranks)?;
            self.phases.push(self.phase(board.clone(),upgrades,vec![],"Buy and combine the required copies before counting these planned stars; no copies or gold are assumed."));
            self.current=Some(board);
        }
        if let Some(constraint)=goal.get("positioning_constraint"){self.current.as_mut().unwrap()["positioning_constraint"]=constraint.clone();}
        Ok(())
    }
    fn ensure(&mut self,i:usize,visiting:&Names)->Result<bool,String>{
        let p=self.planner;if self.approved.contains(&i)||!p.units[i].unlockable||p.confirmed(p.units[i],self.level){return Ok(true)}
        if p.actual(self.level){return Err(format!("Unconfirmed unlock prerequisite for {} at level {}: manually confirm the sourced task before fielding.",p.units[i].name,self.level))}
        if visiting.contains(&i){return Ok(false)}
        if p.units[i].unlock_level.strip_prefix("Level ").and_then(|v|v.parse::<usize>().ok()).is_some_and(|minimum|self.level<minimum){return Ok(false)}
        let recipes=self.recipes;let r=&recipes[&i];
        let dependencies=p.dependencies(&[i].into_iter().collect());
        let mut excluded=self.excluded.clone();
        excluded.extend(self.blocked.iter().filter(|j|!r.candidates.contains(j)&&!dependencies.contains(j)));
        excluded.extend(visiting);
        let mut preferred=self.preferred.clone();if let Some(board)=&self.current{preferred.extend(p.set(&board["champions"]));}
        loop{
            let prep=preparation(p,self.breadth,self.level,i,r,self.target,&excluded,&preferred,&self.pinned)?;
            if prep["status"]=="unavailable"{return Ok(false)}
            let mut failed=None;let mut next_visiting=visiting.clone();next_visiting.insert(i);
            for j in p.set(&prep["board"]["champions"]){if p.units[j].unlockable&&!self.ensure(j,&next_visiting)?{failed=Some(j);break}}
            if let Some(j)=failed{excluded.insert(j);continue}
            if !prep["board"].is_null(){self.move_to(&prep["board"])?;}
            break
        }
        let mut check=unlock(p.units[i]);check["manual"]=json!(r.kind=="manual");
        check["note"]=json!("Hold this setup until the sourced task is actually completed; confirm the unlock before buying the target. No progress is simulated.");
        self.phases.push(self.phase(self.current.clone().unwrap_or(Value::Null),vec![],vec![check],"Conditional unlock check before buying: hold setup helpers until the task is actually complete; no progress is simulated."));
        self.approved.insert(i);Ok(true)
    }
}
fn actual_manual_choices<'a>(source:&'a Value,empty:&'a Map<String,Value>)->&'a Map<String,Value>{
    source["damage_alternative"]["manual_choices"].as_object()
        .or_else(||source["temporary_choices"].as_object()).unwrap_or(empty)
}
struct DamageResume<'a> {
    source: &'a Value,
    level: usize,
    phase: usize,
    selection: Map<String,Value>,
    branch: bool,
}
fn enforce_path(p:&Planner,breadth:&Planner,result:&mut Value,target:&Names,excluded:&Names,blocked:&Names,choices:&Map<String,Value>,resume:Option<&DamageResume>)->Result<(),String>{
    let all:Names=(0..p.units.len()).collect();let recipes=recipes(p,&all,excluded);
    let mut path=OrderedPath{planner:p,breadth,level:1,target,excluded,blocked,preferred:Names::new(),recipes:&recipes,approved:Names::new(),current:None,phases:vec![],pinned:Names::new()};
    let mut pins=BTreeMap::<String,String>::new();let mut actual_slots=Map::new();
    let baseline_slots=result["temporary_slots"].clone();
    // Plan the difficult multi-trait task around eight slots, then retain that
    // useful setup instead of restoring an obsolete incidental arrival.
    let pivot_level=8.min(p.options.size);
    let joint=target.iter().filter(|i|recipes.get(*i).is_some_and(|r|r.kind=="many_traits")&&!p.confirmed(p.units[**i],pivot_level))
        .map(|i|{
            let setup=preparation(p,breadth,pivot_level,*i,&recipes[i],target,excluded,&Names::new(),&Names::new())?;
            if setup["status"]!="conditional_setup"&&pivot_level<p.options.size{
                preparation(p,breadth,pivot_level+1,*i,&recipes[i],target,excluded,&Names::new(),&Names::new())
            }else{Ok(setup)}
        }).collect::<Result<Vec<_>,String>>()?;
    let joint_board=joint.iter().find(|setup|setup["status"]=="conditional_setup").map(|setup|setup["board"].clone());
    let branch_core=result["planning_context"]["branch_core"].as_object().cloned().unwrap_or_default();
    for step in result["buildup"].as_array_mut().unwrap(){
        let level=step["level"].as_u64().unwrap() as usize;let key=level.to_string();path.level=level;path.phases.clear();
        if let Some(resume)=resume.filter(|resume|level<resume.level){
            *step=resume.source["buildup"][level-1].clone();
            merge_pin_choices(&mut pins,choices.get(&key).and_then(Value::as_object).unwrap_or(&Map::new()))?;
            for phase in step["upgrades"].as_array().into_iter().flatten(){
                for check in phase["unlock_checks"].as_array().into_iter().flatten(){
                    if let Some(i)=p.ids.get(text(check,"name")){path.approved.insert(*i);}
                }
            }
            path.current=Some(step["upgrades"].as_array().and_then(|a|a.last()).map_or(&step["board"],|phase|&phase["board"]).clone());
            actual_slots.insert(key.clone(),baseline_slots[&key].clone());
            continue
        }
        if p.actual(level){path.approved=p.confirmed_names(level);}else{path.approved.extend(p.confirmed_names(level));}
        for (name,at) in &branch_core{if at.as_u64().is_some_and(|at|at<=level as u64){pins.entry(name.clone()).or_insert_with(||name.clone());}}
        let empty=Map::new();let selections=choices.get(&key).and_then(Value::as_object).unwrap_or(&empty);
        path.preferred=pins.values().filter_map(|name|p.ids.get(name).copied()).chain(selections.values().filter_map(Value::as_str).filter_map(|name|p.ids.get(name).copied())).collect();
        let desired=step["board"].clone();
        let previous=path.current.as_ref().map(|b|p.set(&b["champions"])).unwrap_or_default();
        let mut safe=Names::new();for (i,c) in p.units.iter().enumerate(){if !excluded.contains(&i)&&(!blocked.contains(&i)||path.approved.contains(&i))&&p.available(c,level)?&&(!c.unlockable||path.approved.contains(&i)){safe.insert(i);}}
        let pending_joint=target.iter().any(|i|recipes.get(i).is_some_and(|r|r.kind=="many_traits")&&!path.approved.contains(i)&&!p.confirmed(p.units[*i],level));
        let route=joint_board.as_ref().filter(|_|level<=pivot_level||pending_joint).map(|b|p.set(&b["champions"])).unwrap_or_default();
        let local=resume.filter(|resume|resume.level==level);
        if let Some(resume)=local{
            let source=&resume.source["buildup"][level-1];
            step["board"]=source["board"].clone();
            path.phases=source["upgrades"].as_array().unwrap()[..resume.phase].to_vec();
            path.current=Some(if resume.phase==0{source["board"].clone()}else{source["upgrades"][resume.phase-1]["board"].clone()});
            for phase in &path.phases{
                for check in phase["unlock_checks"].as_array().into_iter().flatten(){
                    if let Some(i)=p.ids.get(text(check,"name")){path.approved.insert(*i);}
                }
            }
            let source_choices=actual_manual_choices(resume.source,&empty);
            merge_pin_choices(&mut pins,source_choices.get(&key).and_then(Value::as_object).unwrap_or(&empty))?;
            let mut displayed_pins=pins.clone();
            merge_pin_choices(&mut displayed_pins,source["pinned_choices"].as_object().unwrap_or(&empty))?;
            let board=path.current.as_ref().unwrap();
            let canonical:Vec<String>=p.names(&p.set(&board["champions"])).into_iter().map(|name|displayed_pins.iter().find(|(_,chosen)|**chosen==name).map_or(name.clone(),|(slot,_)|slot.clone())).collect();
            let pending:Names=target.difference(&path.approved).copied().collect();
            let dependencies=p.dependencies(&pending);
            let protected=dependencies.iter().map(|i|(*i,[*i].into_iter().collect())).collect();
            let source_target=p.set(&resume.source["champions"]);
            let validation_target=if resume.branch{&source_target}else{target};
            let (alternative,mut options)=temporary_selections(p,level,Some(board.clone()),step.get("fallback"),validation_target,&p.set(&board["champions"]),excluded,blocked,&dependencies,&protected,&canonical,&resume.selection,&displayed_pins)?;
            options.retain(|option|p.ids.get(text(option,"selected")).is_none_or(|i|!target.contains(i)));
            merge_pin_choices(&mut pins,&resume.selection)?;
            path.preferred.extend(pins.values().filter_map(|name|p.ids.get(name).copied()));
            let first_alternative=path.phases.len();
            path.move_to(alternative.as_ref().ok_or("No displayed board is available for this alternative.")?)?;
            for phase in &mut path.phases[first_alternative..]{
                if phase["added"].as_array().is_some_and(|names|!names.is_empty()){
                    phase["reason"]=json!(if resume.branch{"Displayed-board branch: buy the incoming champion before selling the outgoing champion. Retained copies keep their displayed ranks; incoming copies and later upgrades must still be earned."}else{"Displayed-board carry alternative: buy the incoming holder before selling the outgoing holder. Retained copies keep their displayed ranks; incoming copies and later upgrades must still be earned."});
                }
            }
            for &i in &path.approved{
                if !excluded.contains(&i)&&p.available(p.units[i],level)?{safe.insert(i);}
            }
            step["temporary_options"]=json!(options);
            actual_slots.insert(key.clone(),json!(canonical));
        }
        let arrival=if local.is_some(){None}else if !pins.is_empty(){p.pinned_board(level,&safe,target,&previous,&pins)?}
            else if level==pivot_level&&joint_board.as_ref().is_some_and(|b|{let names=p.set(&b["champions"]);names.is_subset(&safe)&&names.iter().map(|i|p.units[*i].slots).sum::<usize>()==level}){joint_board.clone()}
            else if !route.is_empty(){
                let setup_pool:Names=safe.iter().copied().filter(|i|!p.units[*i].unlockable||p.confirmed(p.units[*i],level)||route.contains(i)).collect();
                p.board(level,&setup_pool,target,&previous,&route,false)?
            }
            else if previous.is_empty()&&!desired.is_null()&&p.set(&desired["champions"]).is_subset(&safe){Some(desired.clone())}
            else{p.pinned_board(level,&safe,target,&previous,&pins)?};
        let mut slot_options;
        if local.is_some(){
            slot_options=step["temporary_options"].as_array().unwrap().clone();
        }else{
        let canonical:Vec<String>=if !selections.is_empty(){
            baseline_slots[&key].as_array().ok_or("Missing canonical slot choices; request a fresh recommendation.")?.iter().filter_map(Value::as_str).map(str::to_owned).collect()
        }else{
            arrival.as_ref().map(|board|p.names(&p.set(&board["champions"])).into_iter().map(|name|pins.iter().find(|(_,chosen)|**chosen==name).map_or(name.clone(),|(slot,_)|slot.clone())).collect()).unwrap_or_default()
        };
        let pending:Names=target.iter().copied().filter(|i|!path.approved.contains(i)&&!p.confirmed(p.units[*i],level)).collect();
        let dependencies=p.dependencies(&pending);
        let protected:BTreeMap<usize,Names>=dependencies.iter().map(|i|(*i,[*i].into_iter().collect())).collect();
        let (arrival,options)=temporary_selections(p,level,arrival,step.get("fallback"),target,&previous,excluded,blocked,&dependencies,&protected,&canonical,selections,&pins)?;
        slot_options=options;
        merge_pin_choices(&mut pins,selections)?;
        path.preferred.extend(pins.values().filter_map(|name|p.ids.get(name).copied()));
        path.current=arrival.map(|board|{
            let names=p.set(&board["champions"]);let ranks=names.iter().map(|i|{let name=&p.units[*i].name;
                (name.clone(),path.current.as_ref().and_then(|b|b["planned_stars"][name].as_u64()).unwrap_or(1) as u8)}).collect();
            p.evaluate_stars(level,&names,&ranks)
        }).transpose()?;
        step["board"]=path.current.clone().unwrap_or(Value::Null);
        if let Some(board)=&path.current{
            let names=p.set(&board["champions"]);
            let staged=p.evaluate(level,&names)?;
            path.move_to(&staged)?;
        }
        actual_slots.insert(key.clone(),json!(canonical));
        }
        let core:Names=branch_core.iter().filter(|(_,at)|at.as_u64().is_some_and(|at|at<=level as u64)).filter_map(|(name,_)|p.ids.get(name).copied()).collect();
        path.pinned=core;
        if level<p.options.size{path.pinned.extend(pins.values().map(|name|p.ids[name]));}
        // Only relevant goals and their dependencies are scheduled. Each setup
        // remains the working board until an actual acquisition or final pivot.
        if !p.actual(level){
            let before_setup=path.current.clone();
            for setup in step["preparation_boards"].as_array().unwrap(){
                let i=p.ids[text(setup,"target")];if path.approved.contains(&i)||setup["board"].is_null(){continue}
                if recipes[&i].kind=="many_traits"&&level<pivot_level{continue}
                path.ensure(i,&Names::new())?;
            }
            // Acquire desired quest units without returning every incidental
            // temporary champion from the pre-setup board.
            for i in p.set(&desired["champions"]).intersection(target){
                if p.eligible(p.units[*i],level)?{path.ensure(*i,&Names::new())?;}
            }
            let changed_setup=path.current!=before_setup;
            let holding_joint=level>=pivot_level&&joint_board.is_some()&&target.iter().any(|i|recipes.get(i).is_some_and(|r|r.kind=="many_traits")&&!path.approved.contains(i)&&!p.confirmed(p.units[*i],level));
            let goals=if !step["goal_board"].is_null(){vec![step["goal_board"].clone()]}
                else if !changed_setup&&!holding_joint&&!desired.is_null(){
                    let mut pool=safe.clone();pool.extend(path.approved.iter().copied().filter(|i|p.eligible(p.units[*i],level).unwrap_or(false)));
                    let current=path.current.as_ref().map(|b|p.set(&b["champions"])).unwrap_or_default();
                    p.pinned_board(level,&pool,target,&current,&pins)?.into_iter().collect()
                }else{vec![]};
            for goal in goals{
                for i in p.set(&goal["champions"]){if !path.ensure(i,&Names::new())?{return Err(format!("Unreachable unlock prerequisite for {} at level {level}: no legal setup retains the selected champions.",p.units[i].name))}}
                path.move_to(&goal)?;
            }
        }else if !step["goal_board"].is_null()&&target.iter().all(|i|!p.units[*i].unlockable||p.confirmed(p.units[*i],level)){
            path.move_to(&step["goal_board"])?;
        }
        // A temporary pin is a user constraint, not a score hint. Prerequisite
        // capacity conflicts are explicit; only the fixed final pivot releases it.
        if level<p.options.size{
            for name in pins.values(){
                // Preserved source phases predate the board-local pin. Only its
                // explicit exchange and subsequent phases must retain it.
                let continuation=local.map_or(0,|resume|resume.phase);
                if path.current.as_ref().is_none_or(|b|!p.set(&b["champions"]).contains(&p.ids[name]))||path.phases.iter().skip(continuation).any(|phase|!phase["board"].is_null()&&!p.set(&phase["board"]["champions"]).contains(&p.ids[name])){
                    return Err(format!("Pinned {name} conflicts with the required unlock setup at level {level}; unpin it to use this path."))
                }
            }
        }
        let reached_goal=path.current.as_ref().is_some_and(|board|p.set(&board["champions"])==*target);
        if !step["goal_board"].is_null()&&reached_goal{
            let goal=&step["goal_board"];let mut replacements=Vec::new();
            if let Some(current)=&path.current{for i in p.set(&goal["champions"]){let name=&p.units[i].name;let from=current["planned_stars"][name].as_u64().unwrap_or(1);let to=goal["planned_stars"][name].as_u64().unwrap_or(1);
                if p.options.stars.contains_key(name)&&from>to{replacements.push(json!({"champion":name,"from":from,"to":to}));}
            }}
            if !replacements.is_empty(){
                let mut ranks:BTreeMap<String,u8>=serde_json::from_value(path.current.as_ref().unwrap()["planned_stars"].clone()).map_err(|e|e.to_string())?;
                for row in &replacements{ranks.insert(text(row,"champion").to_owned(),row["to"].as_u64().unwrap() as u8);}
                let board=p.evaluate_stars(level,&p.set(&goal["champions"]),&ranks)?;
                let mut phase=path.phase(board.clone(),vec![],vec![],"Buy fresh copies at the exact final planned ranks before selling the upgraded preparation copies; transfer released items after the sale. No retained copy loses stars.");
                phase["copy_replacements"]=json!(replacements);path.phases.push(phase);path.current=Some(board);
            }
            if let Some(board)=&path.current{step["goal_board"]=board.clone();}
        }
        if level==p.options.size{
            for phase in &mut path.phases{
                let outgoing=p.set(&phase["removed"]);
                let releases:Vec<_>=pins.values().filter(|name|outgoing.contains(&p.ids[*name])).cloned().collect();
                if !releases.is_empty(){
                    phase["pin_releases"]=json!(releases);phase["final_pivot"]=json!(true);
                    let reason=format!("{} Explicit final transition releases the listed temporary pins; secure incoming setup/final units before selling them.",text(phase,"reason"));
                    phase["reason"]=json!(reason);
                }
            }
        }
        let current=p.set(&step["board"]["champions"]);
        step["added"]=json!(p.names(&current.difference(&previous).copied().collect()));
        step["removed"]=json!(p.names(&previous.difference(&current).copied().collect()));
        step["retained"]=json!(p.names(&previous.intersection(&current).copied().collect()));
        step["temporary"]=json!(p.names(&current.difference(target).copied().collect()));
        step["unlocks"]=json!(current.iter().filter(|i|p.units[**i].unlockable).map(|i|unlock(p.units[*i])).collect::<Vec<_>>());
        step["stars"]=step["board"]["planned_stars"].clone();step["star_upgrades"]=json!([]);step["unlock_checks"]=json!([]);
        for option in &mut slot_options{let selected=text(option,"selected").to_owned();option["planned_stars"][&selected]=step["board"]["planned_stars"][&selected].clone();}
        step["temporary_options"]=json!(slot_options);step["pinned_choices"]=json!(pins);
        let mut removed=previous.difference(&current).copied().collect::<Names>();let mut added=current.difference(&previous).copied().collect::<Names>();
        for phase in &path.phases{removed.extend(p.set(&phase["removed"]));added.extend(p.set(&phase["added"]));}
        step["pivot_warning"]=if removed.len()>2||path.phases.iter().filter(|phase|phase["removed"].as_array().is_some_and(|a|!a.is_empty())).count()>2{
            json!({"level":level,"removed":p.names(&removed),"added":p.names(&added),"fast_micro":true,
                "reason":"Large planned pivot: secure incoming units and finish the displayed task checks before selling setup helpers. This may require fast micro; level 8 is a planning window, not an inferred game stage.",
                "sequence":std::iter::once(format!("Buy/add {}; then sell/remove {}; hold the displayed arrival and earn any required stars before the task check.",
                    join_array(&step["added"],", "),join_array(&step["removed"],", "))).chain(path.phases.iter().map(|phase|format!("Buy/add {}; then sell/remove {}; {}",
                    join_array(&phase["added"],", "),join_array(&phase["removed"],", "),text(phase,"reason")))).collect::<Vec<_>>()})
        }else{Value::Null};
        let scheduled:Vec<_>=step["preparation_boards"].as_array().unwrap().iter().enumerate().filter_map(|(index,setup)|{
            let i=p.ids[text(setup,"target")];let recipe=&recipes[&i];
            std::iter::once(&step["board"]).chain(path.phases.iter().map(|phase|&phase["board"])).enumerate()
                .find_map(|(phase,board)|scheduled_setup_helpers(p,level,i,recipe,board).map(|helpers|{
                    let dependencies:Vec<_>=p.set(&board["champions"]).iter().filter(|j|p.units[**j].unlockable&&!p.confirmed(p.units[**j],level)).map(|j|unlock(p.units[*j])).collect();
                    (index,i,phase,helpers,dependencies)
                }))
        }).collect();
        for (index,i,phase,helpers,dependencies) in scheduled{
            let helper_names=p.names(&helpers);
            let setup=&mut step["preparation_boards"][index];
            // Rebind after annotation so helpers, items and hexes share the
            // scheduled phase exactly, without annotating a duplicate board.
            setup["board"]=Value::Null;setup["helpers"]=json!(helper_names);setup["status"]=json!("conditional_setup");
            setup["scheduled"]=json!(true);setup["scheduled_phase"]=json!(phase);
            setup["conditional_dependencies"]=json!(dependencies);
            setup["note"]=json!("This actual scheduled board plans the required roster, ranks or active traits. Earn those copies/ranks and hold the setup for the sourced task. Items, combat progress and task completion still require manual confirmation; this is not a completed unlock.");
            for prep in step["unlock_prep"].as_array_mut().into_iter().flatten(){
                if prep["name"]==p.units[i].name{prep["helpers"]=json!(helper_names);}
            }
        }
        step["upgrades"]=json!(path.phases);
        for phase in step["upgrades"].as_array_mut().unwrap(){
            if phase["board"].is_null(){continue}
            let names=p.set(&phase["board"]["champions"]);
            let canonical:Vec<_>=p.names(&names).into_iter().map(|name|pins.iter().find(|(_,chosen)|**chosen==name).map_or(name.clone(),|(slot,_)|slot.clone())).collect();
            let pending:Names=target.difference(&path.approved).copied().collect();let dependencies=p.dependencies(&pending);
            let protected=dependencies.iter().map(|i|(*i,[*i].into_iter().collect())).collect();
            let (_,mut options)=temporary_selections(p,level,Some(phase["board"].clone()),None,target,&names,excluded,blocked,&dependencies,&protected,&canonical,&Map::new(),&pins)?;
            for option in &mut options{let selected=text(option,"selected").to_owned();option["planned_stars"][&selected]=phase["board"]["planned_stars"][&selected].clone();}
            phase["temporary_options"]=json!(options);
        }
    }
    let Some(end)=path.current else{return Err("Unreachable unlock prerequisite: no exact final board.".into())};
    result["temporary_slots"]=Value::Object(actual_slots);
    if p.set(&end["champions"])!=*target{
        if p.actual(p.options.size){
            let ranks:BTreeMap<String,u8>=target.iter().map(|i|(p.units[*i].name.clone(),p.options.stars.get(&p.units[*i].name).copied().unwrap_or(p.stages[p.options.size-1].stars[*i]))).collect();
            let planned=p.evaluate_stars(p.options.size,target,&ranks)?;
            for key in RESULT_KEYS{result[*key]=planned[*key].clone();}
            return Ok(())
        }
        return Err("Unreachable unlock prerequisite: exact final roster cannot be reached.".into())
    }
    for key in RESULT_KEYS{result[*key]=end[*key].clone();}
    Ok(())
}

pub fn recommend_with_buildups(data:&GameData,options:&OptimizeOptions)->Result<Vec<Value>,String>{
    options.validate(data)?;
    for c in data.champions.values(){if c.unlockable {for (field,text) in [("unlock_condition",&c.unlock_condition),("unlock_level",&c.unlock_level),("unlock_source",&c.unlock_source)]{if text.trim().is_empty(){return Err(format!("{} is missing {field}; refresh the unlock data.",c.name))}}}minimum_level(c)?;}
    let planner=Planner::new(data,options,false)?;
    let locked:Names=options.locked.iter().map(|n|planner.ids[n]).collect();
    let required:Names=locked.iter().copied().chain(planner.assumed(options.size)).collect();
    let blocked=planner.blocked(&required);
    let mut gated=BTreeSet::new();
    for c in data.champions.values(){if !planner.eligible(c,options.size)?{gated.insert(c.name.clone());}}
    for name in &options.locked {if gated.contains(name){let c=&data.champions[name];return Err(format!("{name} cannot be planned at level {}; requires planning level {} or above ({}-cost; published unlock minimum: {}). This is conservative timing, not game impossibility. Unlock task: {}",options.size,planner.minimum_level(c)?,c.cost,if c.unlockable{&c.unlock_level}else{"Any"},if c.unlockable{&c.unlock_condition}else{"No special unlock requirement."}))}}
    let excluded:Names=options.excluded.iter().map(|n|planner.ids[n]).collect();
    let unreachable=globally_unreachable(&planner,&required,&excluded)?;
    for &i in required.intersection(&unreachable){return Err(format!("Unreachable unlock prerequisite for {}: no eligible global setup satisfies {}",planner.units[i].name,planner.units[i].unlock_condition))}
    let conflicting:Names=planner.units.iter().enumerate().filter(|(i,_)|planner.groups[*i]!=0&&required.iter().any(|j|*j!=*i&&planner.groups[*j]==planner.groups[*i])).map(|(i,_)|i).collect();
    let mut final_options=options.clone();final_options.excluded=options.excluded.iter().cloned().chain(gated).chain(planner.names(&blocked)).chain(planner.names(&unreachable)).chain(planner.names(&conflicting)).collect::<BTreeSet<_>>().into_iter().collect();
    let results=optimizer::optimize(data,&final_options)?;
    let context=json!({"collab":options.collab,"size":options.size,"locked":options.locked,"excluded":options.excluded,"min_collab":options.min_collab,"mode":options.mode,"sona_band":options.sona_band,"stars":options.stars,"early_availability":options.early_availability,"completed_unlocks":options.completed_unlocks,"current_level":options.current_level,"no_ad_items":options.no_ad_items,"no_ap_items":options.no_ap_items});
    let mut reachable=Vec::new();let mut failure=None;
    for result in results{match build_buildup(data,result,options,&context,&Map::new(),None,None){
        Ok(result)=>reachable.push(result),
        Err(error) if error.starts_with("Unreachable unlock prerequisite")=>{if failure.is_none(){failure=Some(error);}},
        Err(error)=>return Err(error),
    }}
    if reachable.is_empty()&&options.locked.iter().any(|n|data.champions[n].unlockable){if let Some(error)=failure{return Err(error)}}
    Ok(reachable)
}
fn branch_history_resume(result:&Value,size:usize)->Option<DamageResume<'_>>{
    result.get("branch_selection").and_then(|selection|{
        let level=selection["level"].as_u64()? as usize;let phase=selection["phase"].as_u64()? as usize;
        (selection["champion"].is_string()&&level>0&&level<=size
            &&result["buildup"][level-1]["upgrades"].as_array().is_some_and(|phases|phase<=phases.len()))
            .then_some(DamageResume{source:result,level,phase,selection:Map::new(),branch:true})
    })
}
pub fn replan_buildup(data:&GameData,result:&Value,temporary_choices:Option<&Value>,early_availability:Option<&Value>,completed_unlocks:Option<&Value>,current_level:Option<&Value>)->Result<Value,String>{
    let mut context=result.get("planning_context").filter(|v|v.is_object()).ok_or("This result has no buildup planning context; request a fresh recommendation.")?.clone();
    let previous=OptimizeOptions::from_value(&context)?;
    for (key,value) in [("early_availability",early_availability),("completed_unlocks",completed_unlocks),("current_level",current_level)]{
        if let Some(value)=value.filter(|v|!v.is_null()){context[key]=value.clone();}
    }
    let options=OptimizeOptions::from_value(&context)?;options.validate(data)?;
    context["early_availability"]=json!(options.early_availability);context["completed_unlocks"]=json!(options.completed_unlocks);context["current_level"]=json!(options.current_level);
    let changed=previous.early_availability!=options.early_availability;
    let empty=Map::new();
    let choices=match temporary_choices {
        Some(v) if !v.is_null()=>v.as_object().ok_or("temporary_choices must be an object keyed by level strings.")?,
        Some(_)=>result.get("temporary_choices").and_then(Value::as_object).unwrap_or(&empty),
        None=>result.get("temporary_choices").and_then(Value::as_object).unwrap_or(&empty),
    };
    if changed&&!choices.is_empty(){return Err("Changing early_availability requires clearing temporary_choices with {} before choosing new scenario slots.".into())}
    for (level,selections) in choices {
        if !(1..=options.size).any(|n|n.to_string()==*level){return Err(format!("Invalid temporary-choice level: {level:?}."))}
        if selections.as_object().is_none_or(|s|s.values().any(|v|!v.is_string())){return Err("Each temporary choice must map canonical slot names to champion names.".into())}
    }
    let slots=result.get("temporary_slots").and_then(Value::as_object).ok_or("This result has no temporary slots; request a fresh recommendation.")?;
    let resume=if !changed&&result.get("temporary_choices").and_then(Value::as_object)==Some(choices){
        branch_history_resume(result,options.size)
    }else{None};
    build_buildup(data,result.clone(),&options,&context,choices,if changed{None}else{Some(slots)},resume.as_ref())
}
/// Adapt only the selected result, resuming from the board the user actually viewed.
pub fn choose_branch(data:&GameData,payload:&Value)->Result<Value,String>{
    let object=payload.as_object().ok_or("choose_branch must be an object.")?;
    let source=object.get("result").filter(|v|v.is_object()).ok_or("Missing result for branch selection.")?;
    let level=object.get("level").and_then(Value::as_u64).filter(|n|(1..=11).contains(n)).ok_or("level must be an integer from 1 to 11.")? as usize;
    let phase=object.get("phase").and_then(Value::as_u64).and_then(|n|usize::try_from(n).ok()).ok_or("phase must be a nonnegative integer.")?;
    let slot=object.get("slot").and_then(Value::as_str).filter(|name|data.champions.contains_key(*name)).ok_or("slot must be a canonical champion name.")?;
    let champion=match object.get("champion"){
        Some(Value::Null)=>None,
        Some(Value::String(name)) if data.champions.contains_key(name)=>Some(name.as_str()),
        _=>return Err("champion must be a canonical champion name or null.".into()),
    };
    let choices=object.get("temporary_choices").and_then(Value::as_object).ok_or("temporary_choices must be an object keyed by level strings.")?;
    let context=source.get("planning_context").filter(|v|v.is_object()).ok_or("This result has no buildup planning context; request a fresh recommendation.")?;
    let options=OptimizeOptions::from_value(context)?;options.validate(data)?;
    let goal=crate::model::names(&source["champions"],"champions")?;
    if goal.iter().any(|name|!data.champions.contains_key(name))||goal.iter().map(|name|data.champions[name].slots).sum::<usize>()!=options.size{
        return Err("The source result must contain a valid exact-slot final roster.".into())
    }
    let steps=source["buildup"].as_array().filter(|steps|steps.len()==options.size).ok_or("This result has no complete buildup history.")?;
    if steps.iter().enumerate().any(|(index,step)|step["level"].as_u64()!=Some((index+1) as u64)||!step["upgrades"].is_array()){
        return Err("The source result has malformed buildup levels or phase history.".into())
    }
    let step=steps.get(level-1).ok_or("The displayed level is outside this buildup.")?;
    let upgrades=step["upgrades"].as_array().unwrap();
    let displayed=if phase==0{&step["board"]}else{&upgrades.get(phase-1).ok_or("The displayed phase is outside this level.")?["board"]};
    let displayed_names=crate::model::names(&displayed["champions"],"displayed champions")?;
    if displayed_names.iter().any(|name|!data.champions.contains_key(name))||displayed_names.iter().map(|name|data.champions[name].slots).sum::<usize>()!=level{
        return Err("The displayed phase has no valid exact-slot board.".into())
    }
    for (key,selections) in choices{
        if !(1..=options.size).any(|n|n.to_string()==*key)||selections.as_object().is_none_or(|selections|selections.iter().any(|(slot,name)|!data.champions.contains_key(slot)||name.as_str().is_none_or(|name|!data.champions.contains_key(name)))){
            return Err("Each temporary choice must map canonical slot names to champion names at a valid level.".into())
        }
    }
    let empty=Map::new();let old_choices=actual_manual_choices(source,&empty);
    for n in 1..level{
        let key=n.to_string();
        if choices.get(&key)!=old_choices.get(&key){return Err("Branch selection cannot rewrite earlier manual choices.".into())}
    }
    let key=level.to_string();
    let selection=choices.get(&key).and_then(Value::as_object).and_then(|row|row.get(slot)).and_then(Value::as_str);
    if selection!=champion{return Err("temporary_choices must contain the selected champion, or omit the cleared slot.".into())}
    let old_row=old_choices.get(&key).and_then(Value::as_object).unwrap_or(&empty);
    let row=choices.get(&key).and_then(Value::as_object).unwrap_or(&empty);
    if old_row.iter().filter(|(name,_)|name.as_str()!=slot).any(|(name,value)|row.get(name)!=Some(value))
        ||row.iter().filter(|(name,_)|name.as_str()!=slot).any(|(name,value)|old_row.get(name)!=Some(value)){
        return Err("Branch selection cannot rewrite other choices at the displayed level.".into())
    }
    let p=Planner::for_goal_path(data,&options,false,&goal,old_choices,context["path_reroll"].as_bool())?;
    let target=p.set(&source["champions"]);let current=p.set(&displayed["champions"]);
    let excluded:Names=options.excluded.iter().map(|name|p.ids[name]).collect();
    let required=options.locked.iter().map(|name|p.ids[name]).chain(p.assumed(options.size)).collect();
    let blocked=p.blocked(&required);let dependencies=p.dependencies(&target);
    let protected=dependencies.iter().map(|i|(*i,[*i].into_iter().collect())).collect();
    let mut pins=BTreeMap::new();
    for n in 1..=level{if let Some(selections)=old_choices.get(&n.to_string()).and_then(Value::as_object){merge_pin_choices(&mut pins,selections)?;}}
    let mut displayed_pins=pins.clone();
    merge_pin_choices(&mut displayed_pins,step["pinned_choices"].as_object().unwrap_or(&empty))?;
    let canonical:Vec<_>=displayed_names.iter().map(|name|displayed_pins.iter().find(|(_,chosen)|*chosen==name).map_or(name.clone(),|(slot,_)|slot.clone())).collect();
    if !canonical.iter().any(|name|name==slot){return Err("The canonical slot is not present on the displayed board.".into())}
    if champion.is_none(){
        let mut result=replan_buildup(data,source,Some(&Value::Object(choices.clone())),None,None,None)?;
        result["branch_selection"]=json!({"level":level,"phase":0,"slot":slot,"champion":null,"previous_final":goal,"final":result["champions"]});
        return Ok(result)
    }
    let champion=champion.unwrap();
    let selection=Map::from_iter([(slot.to_owned(),json!(champion))]);
    // Validate against the old Final before adding new locks.
    temporary_selections(&p,level,Some(displayed.clone()),step.get("fallback"),&target,&current,&excluded,&blocked,&dependencies,&protected,&canonical,&selection,&displayed_pins)?;
    let mut updated_context=context.clone();
    updated_context["path_reroll"]=json!(context["path_reroll"].as_bool().unwrap_or_else(||optimizer::reroll_plan(data,&goal,&options.stars)));
    let mut core=context["branch_core"].as_object().cloned().unwrap_or_default();
    for name in displayed_names.iter().filter(|name|goal.contains(name)){core.entry(name.clone()).or_insert_with(||json!(level));}
    updated_context["branch_core"]=json!(core);
    let mut constrained=options.clone();
    constrained.locked=options.locked.iter().cloned().chain(core.keys().cloned()).collect::<BTreeSet<_>>().into_iter().collect();
    let commitment=data.champions[champion].cost>=3||options.stars.get(champion).is_some_and(|rank|*rank>=3);
    if commitment&&!constrained.locked.iter().any(|name|name==champion){
        let occupied:usize=constrained.locked.iter().map(|name|data.champions[name].slots).sum();
        if occupied+data.champions[champion].slots<=options.size{constrained.locked.push(champion.to_owned());}
    }
    let final_planner=Planner::new(data,&constrained,false)?;
    let required:Names=constrained.locked.iter().map(|name|final_planner.ids[name]).chain(final_planner.assumed(options.size)).collect();
    let blocked=final_planner.blocked(&required);let unreachable=globally_unreachable(&final_planner,&required,&excluded)?;
    constrained.excluded=options.excluded.iter().cloned().chain(final_planner.units.iter().enumerate()
        .filter(|(i,c)|blocked.contains(i)||unreachable.contains(i)||!final_planner.eligible(c,options.size).unwrap_or(false))
        .map(|(_,c)|c.name.clone())).collect::<BTreeSet<_>>().into_iter().collect();
    for name in &goal{if let Some(rank)=source["planned_stars"][name].as_u64(){constrained.stars.entry(name.clone()).or_insert(rank as u8);}}
    constrained.limit=12;
    let mut candidates=optimizer::optimize(data,&constrained)?;
    let branch=p.ids[champion];
    let affinity=|board:&Value|{
        let names=p.set(&board["champions"]);
        let overlap=names.iter().filter(|i|**i!=branch).map(|i|p.unit_traits[*i].intersection(&p.unit_traits[branch]).count()).sum::<usize>();
        let quests=recipes(&p,&names,&excluded).iter().filter(|(i,r)|!p.confirmed(p.units[**i],options.size)&&(r.candidates.contains(&branch)||p.dependencies(&[**i].into_iter().collect()).contains(&branch))).count();
        (quests,overlap)
    };
    if !commitment{candidates.sort_by_cached_key(|board|std::cmp::Reverse(affinity(board)));}
    let slots=source["temporary_slots"].as_object().ok_or("This result has no temporary slots; request a fresh recommendation.")?;
    let mut updated=options.clone();
    updated.stars=constrained.stars.clone();updated_context["stars"]=json!(updated.stars);
    let resume=DamageResume{source,level,phase,selection,branch:true};
    let mut failure=None;
    for board in candidates{
        let mut selected=source.clone();selected["champions"]=board["champions"].clone();
        match build_buildup(data,selected,&updated,&updated_context,choices,Some(slots),Some(&resume)){
            Ok(mut result)=>{
                let end_step=&result["buildup"][options.size-1];
                let end=end_step["upgrades"].as_array().unwrap().last().map_or(&end_step["board"],|phase|&phase["board"]);
                if end["champions"]!=result["champions"]||end["planned_stars"]!=result["planned_stars"]{
                    failure=Some("The adaptive Final cannot be reached with the actual unlock confirmations and exact planned ranks.".into());continue
                }
                for n in 0..level-1{result["buildup"][n]=steps[n].clone();}
                result["buildup"][level-1]["board"]=step["board"].clone();
                for n in 0..phase{result["buildup"][level-1]["upgrades"][n]=upgrades[n].clone();}
                let output_phase=if displayed_names.iter().any(|name|name==champion){phase}else{phase+1};
                result["branch_selection"]=json!({"level":level,"phase":output_phase,"slot":slot,"champion":champion,"previous_final":goal,"final":result["champions"]});
                result.as_object_mut().unwrap().remove("damage_alternative");
                return Ok(result)
            },
            Err(error)=>failure=Some(error),
        }
    }
    Err(failure.unwrap_or_else(||"No legal adaptive Final fits the selected branch, fielded Final core, global locks, exclusions and Collab minimum.".into()))
}
/// Change one selected board, never the ranked recommendation list.
pub fn damage_alternative(data:&GameData,payload:&Value)->Result<Value,String>{
    let object=payload.as_object().ok_or("damage_alternative must be an object.")?;
    let source=object.get("result").filter(|v|v.is_object()).ok_or("Missing result for the displayed-board alternative.")?;
    let flag=|key:&str|object.get(key).and_then(Value::as_bool).ok_or_else(||format!("{key} must be a boolean."));
    let no_ad=flag("no_ad_items")?;let no_ap=flag("no_ap_items")?;
    let phase=match object.get("phase"){None=>0,Some(value)=>usize::try_from(value.as_u64().ok_or("phase must be a nonnegative integer.")?).map_err(|_|"phase is outside the supported range.")?};
    let level=match object.get("level"){None|Some(Value::Null)=>None,Some(value)=>Some(value.as_u64().filter(|n|*n>0&&*n<=11).ok_or("level must be null or an integer from 1 to 11.")? as usize)};
    if level.is_none()&&phase!=0{return Err("Final alternatives require phase 0.".into())}
    let context=source.get("planning_context").filter(|v|v.is_object()).ok_or("This result has no buildup planning context; request a fresh recommendation.")?;
    let options=OptimizeOptions::from_value(context)?;options.validate(data)?;
    let goal=crate::model::names(&source["champions"],"champions")?;
    if goal.iter().any(|name|!data.champions.contains_key(name))||goal.iter().map(|name|data.champions[name].slots).sum::<usize>()!=options.size{
        return Err("The source result must contain a valid exact-slot final roster.".into())
    }
    let steps=source.get("buildup").and_then(Value::as_array).filter(|steps|steps.len()==options.size).ok_or("This result has no complete buildup history.")?;
    let empty=Map::new();
    let choices=source.get("temporary_choices").and_then(Value::as_object).unwrap_or(&empty);
    if steps.iter().enumerate().any(|(index,step)|step["level"].as_u64()!=Some((index+1) as u64)||!step["upgrades"].is_array()){
        return Err("The source result has malformed buildup levels or phase history.".into())
    }
    for (level,selections) in choices{
        if !(1..=options.size).any(|n|n.to_string()==*level)||selections.as_object().is_none_or(|selections|selections.iter().any(|(slot,name)|!data.champions.contains_key(slot)||name.as_str().is_none_or(|name|!data.champions.contains_key(name)))){
            return Err("The source result has malformed temporary choices.".into())
        }
    }
    let compatible=|name:&str|{let profile=guidance::item_profile(&data.champions[name]);profile=="flex"||profile==if no_ad{"ap"}else{"ad"}};
    let manual_choices=actual_manual_choices(source,&empty);
    let metadata=|level:Option<usize>,phase:usize|json!({"level":level,"phase":phase,"no_ad_items":no_ad,"no_ap_items":no_ap,"manual_choices":manual_choices});
    if let Some(level)=level{
        let step=steps.get(level-1).ok_or("The displayed level is outside this buildup.")?;
        let upgrades=step["upgrades"].as_array().ok_or("The displayed level has no upgrade history.")?;
        let displayed=if phase==0{&step["board"]}else{&upgrades.get(phase-1).ok_or("The displayed phase is outside this level.")?["board"]};
        let displayed_names=crate::model::names(&displayed["champions"],"displayed champions")?;
        if displayed_names.iter().any(|name|!data.champions.contains_key(name))||displayed_names.iter().map(|name|data.champions[name].slots).sum::<usize>()!=level{
            return Err("The displayed phase has no valid exact-slot board.".into())
        }
        if level==options.size&&displayed_names.iter().cloned().collect::<BTreeSet<_>>()==goal.iter().cloned().collect(){
            let mut result=damage_alternative(data,&json!({"result":source,"level":null,"phase":0,"no_ad_items":no_ad,"no_ap_items":no_ap}))?;
            let final_board=result["buildup"][level-1]["goal_board"].clone();
            let updated=OptimizeOptions::from_value(&result["planning_context"])?;
            let p=Planner::for_goal(data,&updated,false,&crate::model::names(&result["champions"],"champions")?,choices)?;
            let target=p.set(&result["champions"]);let excluded=updated.excluded.iter().map(|name|p.ids[name]).collect();
            let blocked=p.blocked(&updated.locked.iter().map(|name|p.ids[name]).collect());
            let all=(0..p.units.len()).collect();let recipes=recipes(&p,&all,&excluded);
            let mut approved=p.confirmed_names(level);
            for old in steps{
                for check_phase in old["upgrades"].as_array().into_iter().flatten(){
                    for check in check_phase["unlock_checks"].as_array().into_iter().flatten(){
                        if let Some(i)=p.ids.get(text(check,"name")){approved.insert(*i);}
                    }
                }
            }
            let mut path=OrderedPath{planner:&p,breadth:&p,level,target:&target,excluded:&excluded,blocked:&blocked,preferred:Names::new(),recipes:&recipes,approved,current:Some(displayed.clone()),phases:vec![],pinned:Names::new()};
            for &i in &target{if !path.ensure(i,&Names::new())?{return Err(format!("Unreachable unlock prerequisite for {} at level {level}: no legal setup retains the selected champions.",p.units[i].name))}}
            path.move_to(&final_board)?;
            let output_phase=if path.phases.is_empty(){phase}else{phase+path.phases.len()};
            result["buildup"]=source["buildup"].clone();
            result["buildup"][level-1]["upgrades"]=json!(upgrades[..phase].iter().cloned().chain(path.phases).collect::<Vec<_>>());
            result["buildup"][level-1]["goal_board"]=final_board;
            guidance::annotate_result(&mut result,data)?;
            trait_support::annotate_trait_support(&mut result,data)?;
            positioning::annotate_positioning(&mut result,data)?;
            for n in 0..level-1{result["buildup"][n]=steps[n].clone();}
            result["buildup"][level-1]["board"]=step["board"].clone();
            for n in 0..phase{result["buildup"][level-1]["upgrades"][n]=upgrades[n].clone();}
            result["damage_alternative"]=metadata(Some(level),output_phase);
            return Ok(result)
        }
        if no_ad==no_ap||displayed["item_holders"][if no_ad{"ad"}else{"ap"}].is_null(){
            let mut board=displayed.clone();annotate_damage_board(&mut board,data,no_ad,no_ap)?;
            let mut result=source.clone();
            if phase==0{result["buildup"][level-1]["board"]=board}else{result["buildup"][level-1]["upgrades"][phase-1]["board"]=board}
            result["damage_alternative"]=metadata(Some(level),phase);
            return Ok(result)
        }
        let p=Planner::for_goal(data,&options,false,&goal,choices)?;
        let target=p.set(&source["champions"]);let current=p.set(&displayed["champions"]);
        let excluded=options.excluded.iter().map(|name|p.ids[name]).collect();
        let required=options.locked.iter().map(|name|p.ids[name]).chain(p.assumed(options.size)).collect();
        let blocked=p.blocked(&required);
        let dependencies=p.dependencies(&target);
        let protected=dependencies.iter().copied().chain(options.locked.iter().map(|name|p.ids[name])).map(|i|(i,[i].into_iter().collect())).collect();
        let mut pins=BTreeMap::new();
        for n in 1..=level{if let Some(selections)=choices.get(&n.to_string()).and_then(Value::as_object){merge_pin_choices(&mut pins,selections)?;}}
        let canonical:Vec<_>=displayed_names.iter().map(|name|pins.iter().find(|(_,chosen)|*chosen==name).map_or(name.clone(),|(slot,_)|slot.clone())).collect();
        let (_,offered)=temporary_selections(&p,level,Some(displayed.clone()),step.get("fallback"),&target,&current,&excluded,&blocked,&dependencies,&protected,&canonical,&Map::new(),&pins)?;
        let mut candidates=Vec::new();
        let holder=displayed["item_holders"][if no_ad{"ad"}else{"ap"}].as_str().unwrap_or("");
            for option in &offered{
                let selected=text(option,"selected");
                if selected!=holder||options.locked.iter().any(|name|name==selected){continue}
                for name in option["options"].as_array().into_iter().flatten().filter_map(Value::as_str){
                    if name!=selected&&compatible(name){
                        candidates.push(Map::from_iter([(text(option,"slot").to_owned(),json!(name))]));
                    }
                }
            }
        let slots=source.get("temporary_slots").and_then(Value::as_object).ok_or("This result has no temporary slots.")?;
        let mut failure=None;
        for selection in candidates{
            let mut updated=choices.clone();
            if !selection.is_empty(){
                let entry=updated.entry(level.to_string()).or_insert_with(||json!({})).as_object_mut().ok_or("Invalid temporary choices.")?;
                entry.extend(selection.clone());
            }
            let resume=DamageResume{source,level,phase,selection,branch:false};
            match build_buildup(data,source.clone(),&options,context,&updated,Some(slots),Some(&resume)){
                Ok(mut result)=>{
                    let output_phase=if resume.selection.is_empty(){phase}else{phase+1};
                    // Item assumptions are local to this continuation, not a new Final.
                    for step in result["buildup"].as_array_mut().unwrap().iter_mut().skip(level-1){
                        if !step["board"].is_null(){annotate_damage_board(&mut step["board"],data,no_ad,no_ap)?;}
                        for upgrade in step["upgrades"].as_array_mut().unwrap(){
                            annotate_damage_board(&mut upgrade["board"],data,no_ad,no_ap)?;
                        }
                    }
                    for (key,value) in source.as_object().unwrap(){
                        if !["buildup","temporary_choices","temporary_slots","buildup_notes","damage_alternative"].contains(&key.as_str()){result[key]=value.clone();}
                    }
                    for n in 0..level-1{result["buildup"][n]=steps[n].clone();}
                    result["buildup"][level-1]["board"]=step["board"].clone();
                    for n in 0..phase{result["buildup"][level-1]["upgrades"][n]=upgrades[n].clone();}
                    result["buildup"][options.size-1]["goal_board"]=steps[options.size-1]["goal_board"].clone();
                    result["damage_alternative"]=metadata(Some(level),output_phase);
                    return Ok(result)
                },
                Err(error)=>failure=Some(error),
            }
        }
        return Err(failure.unwrap_or_else(||"No legal board-local carry alternative exists for this displayed phase under its locks, availability gates and unlock setup.".into()))
    }
    let mut updated_context=context.clone();updated_context["no_ad_items"]=json!(no_ad);updated_context["no_ap_items"]=json!(no_ap);
    let mut updated=options.clone();updated.no_ad_items=no_ad;updated.no_ap_items=no_ap;
    let incompatible=if no_ad{"ad"}else{"ap"};
    let holders:Vec<_>=goal.iter().filter(|name|no_ad!=no_ap&&guidance::item_profile(&data.champions[*name])==incompatible
        &&source["item_holders"][incompatible].as_str()==Some(name.as_str())&&!options.locked.contains(name)).cloned().collect();
    let needs_swap=no_ad!=no_ap&&source["item_holders"][incompatible].is_string()&&!goal.iter().any(|name|compatible(name));
    let mut failure=None;
    for count in (if needs_swap{1}else{0})..=holders.len(){
        let indices:Vec<_>=(0..holders.len()).collect();
        for removed in subsets(&indices,count){
            let removed:BTreeSet<_>=removed.iter().map(|i|holders[*i].clone()).collect();
            let mut constrained=updated.clone();
            constrained.locked=goal.iter().filter(|name|!removed.contains(*name)).cloned().chain(options.locked.iter().cloned()).collect::<BTreeSet<_>>().into_iter().collect();
            let p=Planner::new(data,&constrained,false)?;
            let required=constrained.locked.iter().map(|name|p.ids[name]).chain(p.assumed(options.size)).collect();
            let excluded=options.excluded.iter().map(|name|p.ids[name]).collect();
            let blocked=p.blocked(&required);let unreachable=globally_unreachable(&p,&required,&excluded)?;
            constrained.excluded=options.excluded.iter().cloned().chain(removed.iter().cloned())
                .chain(p.units.iter().enumerate().filter(|(i,c)|blocked.contains(i)||unreachable.contains(i)||!p.eligible(c,options.size).unwrap_or(false)).map(|(_,c)|c.name.clone())).collect::<BTreeSet<_>>().into_iter().collect();
            // Retained ranks are assumptions of this exact source roster.
            for name in goal.iter().filter(|name|!removed.contains(*name)){
                if let Some(rank)=source["planned_stars"][name].as_u64(){constrained.stars.insert(name.clone(),rank as u8);}
            }
            constrained.limit=12;
            let boards=match optimizer::optimize(data,&constrained){Ok(boards)=>boards,Err(error)=>{failure=Some(error);continue}};
            for board in boards{
                let incoming=crate::model::names(&board["champions"],"champions")?;
                if !removed.is_empty()&&!incoming.iter().any(|name|!goal.contains(name)&&compatible(name)){continue}
                let mut selected=source.clone();selected["champions"]=board["champions"].clone();
                updated.stars=constrained.stars.clone();updated_context["stars"]=json!(updated.stars);
                let resume=branch_history_resume(source,options.size);
                match build_buildup(data,selected,&updated,&updated_context,choices,source.get("temporary_slots").and_then(Value::as_object),resume.as_ref()){
                    Ok(mut result)=>{
                        if result.get("branch_selection").is_some_and(Value::is_object){result["branch_selection"]["final"]=result["champions"].clone();}
                        result["damage_alternative"]=metadata(None,0);return Ok(result)
                    },
                    Err(error)=>failure=Some(error),
                }
            }
        }
    }
    Err(failure.unwrap_or_else(||"No minimal board-local carry alternative fits the locked roster, exclusions, Collab minimum and availability gates.".into()))
}

fn annotate_damage_board(board:&mut Value,data:&GameData,no_ad:bool,no_ap:bool)->Result<(),String>{
    board["planning_context"]=json!({"no_ad_items":no_ad,"no_ap_items":no_ap});
    crate::item_builds::annotate_item_builds(board,data)?;
    board.as_object_mut().unwrap().remove("planning_context");
    Ok(())
}

fn build_buildup(data:&GameData,mut result:Value,options:&OptimizeOptions,context:&Value,choices:&Map<String,Value>,baseline_slots:Option<&Map<String,Value>>,resume:Option<&DamageResume>)->Result<Value,String>{
    let goal_names=crate::model::names(&result["champions"],"champions")?;
    let path_reroll=context["path_reroll"].as_bool();
    let p=Planner::for_goal_path(data,options,false,&goal_names,choices,path_reroll)?;
    let locked:Names=options.locked.iter().map(|n|p.ids[n]).collect();let excluded:Names=options.excluded.iter().map(|n|p.ids[n]).collect();
    let default_blocked=p.blocked(&Names::new());
    let target=p.set(&result["champions"]);let assumed=p.assumed(options.size);
    for &i in &assumed {
        if target.iter().any(|j|*j!=i&&p.groups[i]!=0&&p.groups[*j]==p.groups[i]){
            return Err(format!("Early availability for {} conflicts with the fixed final roster.",p.units[i].name))
        }
    }
    for &i in &target {if excluded.contains(&i)||!p.eligible(p.units[i],options.size)?{return Err(format!("{} cannot reach the fixed final roster under these availability gates.",p.units[i].name))}}
    let required:Names=locked.union(&assumed).copied().collect();let blocked=p.blocked(&required);
    let unreachable=globally_unreachable(&p,&required,&excluded)?;
    for &i in assumed.intersection(&unreachable){return Err(format!("Unreachable unlock prerequisite for {}: no eligible global setup satisfies {}",p.units[i].name,p.units[i].unlock_condition))}
    let scenario_goals:Names=target.union(&assumed).copied().collect();let mut dependencies=p.dependencies(&scenario_goals);
    if target.iter().any(|i|p.units[*i].name=="Teemo"||locked.contains(i)&&p.units[*i].traits.iter().any(|t|t=="Hyperpop")){
        dependencies.extend(p.units.iter().enumerate().filter(|(_,c)|c.traits.iter().any(|t|t=="Hyperpop")).map(|(i,_)|i));
    }
    let all_goals:Names=scenario_goals.union(&dependencies).copied().collect();let recipes=recipes(&p,&all_goals,&excluded);
    let breadth=if recipes.values().any(|r|r.kind=="many_traits"){Some(Planner::for_goal_path(data,options,true,&goal_names,choices,path_reroll)?)}else{None};
    let mut prepared=Names::new();let mut previous=Names::new();let mut previous_fallback=Names::new();let mut steps=Vec::new();let mut slots_by_level=baseline_slots.cloned().unwrap_or_default();
    result["planning_context"]=context.clone();result["early_availability"]=json!(options.early_availability);result["completed_unlocks"]=json!(options.completed_unlocks);result["current_level"]=json!(options.current_level);result["temporary_choices"]=Value::Object(choices.clone());result["collab_pool"]=json!(options.collab.iter().filter(|n|n.as_str()!="Sona").collect::<Vec<_>>());result["sona_band"]=json!(options.sona_band);
    // Rebuild the same roster from context defaults, not scenario-polluted result
    // ranks. Enforcement reintroduces any mandatory preparation floors.
    let mut final_ranks:BTreeMap<String,u8>=target.iter().map(|i|(p.units[*i].name.clone(),p.stages[options.size-1].stars[*i])).collect();
    for (name,rank) in &options.stars {if let Some(final_rank)=final_ranks.get_mut(name){*final_rank=*rank;}}
    let final_goal=p.evaluate_stars(options.size,&target,&final_ranks)?;
    for level in 1..=options.size {
        let final_level=level==options.size;
        let pending:Names=scenario_goals.difference(&previous).copied().filter(|i|p.units[*i].unlockable&&!p.confirmed(p.units[*i],level)).collect();
        let mut useful=p.dependencies(&pending);
        for (&i,r) in &recipes {if ["distinct","active"].contains(&r.kind)&&r.traits.iter().any(|t|previous.iter().filter(|j|r.candidates.contains(j)&&p.unit_traits[**j].contains(t)).count()>=r.count){prepared.insert(i);}}
        let mut waiting:Names=recipes.iter().filter(|(i,r)|["distinct","active"].contains(&r.kind)&&!prepared.contains(*i)&&!previous.contains(*i)&&!p.confirmed(p.units[**i],level)).map(|(i,_)|*i).collect();
        let focus=pending.iter().filter(|i|recipes.contains_key(*i)&&!prepared.contains(*i)).min_by_key(|i|(!assumed.contains(*i),p.minimum_level(p.units[**i]).unwrap_or(usize::MAX),**i));
        if let Some(i)=focus {let r=&recipes[i];if ["distinct","active","named","holder","stars","role"].contains(&r.kind){let mut count=0;for &j in &r.candidates{if !excluded.contains(&j)&&p.eligible(p.units[j],level)?&&count<r.count{useful.insert(j);count+=1;}}}}
        if pending.iter().any(|i|p.units[*i].name=="Teemo"){useful.extend(dependencies.difference(&target).copied().filter(|i|p.units[*i].traits.iter().any(|t|t=="Hyperpop")));}
        useful.extend(p.dependencies(&useful.difference(&previous).copied().collect()));
        for &i in all_goals.difference(&previous){if p.units[i].unlockable&&!p.confirmed(p.units[i],level){let named=p.helpers(&[i].into_iter().collect());if !named.is_empty()&&named.is_disjoint(&previous){waiting.insert(i);}}}
        let prepare_sona=pending.iter().any(|i|p.units[*i].name=="Sona");
        let mut eligible_primary=Names::new();let mut eligible_fallback=Names::new();
        for (i,c) in p.units.iter().enumerate(){if excluded.contains(&i)||!p.available(c,level)?{continue}
            if (!blocked.contains(&i)||dependencies.contains(&i))&&(!waiting.contains(&i)||assumed.contains(&i))&&(!c.unlockable||all_goals.contains(&i)){eligible_primary.insert(i);}
            if !blocked.contains(&i)&&!c.unlockable{eligible_fallback.insert(i);}
        }
        let board=p.board(level,&eligible_primary,&target,&previous,&useful,prepare_sona)?;
        let fallback=p.board(level,&eligible_fallback,&target,&previous_fallback,&Names::new(),prepare_sona)?;
        let key=level.to_string();
        slots_by_level.entry(key.clone()).or_insert_with(||json!(board.as_ref().map(|b|p.names(&p.set(&b["champions"]))).unwrap_or_default()));
        let empty=Map::new();let selections=choices.get(&key).and_then(Value::as_object).unwrap_or(&empty);
        let temporary_options:Vec<Value>=vec![];
        let preferred:Names=selections.values().filter_map(Value::as_str).filter_map(|n|p.ids.get(n).copied()).collect();
        let preparation_boards=recipes.iter().filter(|(i,_)|!p.confirmed(p.units[**i],level)).map(|(&i,r)|preparation(&p,breadth.as_ref().unwrap_or(&p),level,i,r,&target,&excluded,&preferred,&Names::new())).collect::<Result<Vec<_>,_>>()?;
        let current=board.as_ref().map(|b|p.set(&b["champions"])).unwrap_or_default();
        let goal=if final_level{Some(final_goal.clone())}else{None};
        let upgrades:Vec<Value>=vec![];
        let prep_goals:Names=scenario_goals.iter().copied().chain(useful.intersection(&dependencies).copied()).collect();let mut prep=Vec::new();
        for &i in prep_goals.difference(&current){if !p.units[i].unlockable||p.confirmed(p.units[i],level){continue}let mut item=unlock(p.units[i]);item["helpers"]=json!(p.names(&p.dependencies(&[i].into_iter().collect()).intersection(&current).copied().collect()));
            if let Some(r)=recipes.get(&i){if r.distinct {item["traits"]=json!(r.traits.iter().map(|t|p.trait_names[*t]).collect::<Vec<_>>());item["count"]=json!(r.count);item["count_mode"]=json!("distinct_champions");item["helpers"]=json!(p.names(&current.iter().copied().filter(|j|r.candidates.contains(j)).collect()));}}
            if p.units[i].name=="Teemo"{item["helpers"]=json!(p.names(&current.intersection(&dependencies).copied().filter(|j|p.units[*j].traits.iter().any(|t|t=="Hyperpop")).collect()));}prep.push(item);
        }
        let unlocks:Vec<_>=current.iter().filter(|i|p.units[**i].unlockable).map(|i|unlock(p.units[*i])).collect();
        let mut milestones=Vec::<String>::new();
        if board.is_none(){milestones.push("No eligible exact-slot primary board at this level; exclusions, shop access, weighted slots or exclusivity prevent it.".into());}
        if fallback.is_none(){milestones.push("No exact-slot no-unlock alternative exists at this level under these exclusions and slot constraints.".into());}
        if !unlocks.is_empty(){milestones.push("Conditional plan only: complete each listed task before fielding its champion; quest progress is not assumed or simulated.".into());}
        for item in &prep {
            milestones.push(format!("Unlock prep for {}: {} Required stars, items and progress must be earned, not inferred from this board. Keep useful setup helpers; replace after unlock is complete.",text(item,"name"),text(item,"condition")));
            if item["count_mode"]=="distinct_champions"{milestones.push(format!("{}: field {} distinct champions from {}; planned stars do not replace distinct units. Play the required combat / confirm the unlock, then buy the champion; the roster alone does not complete the task.",text(item,"name"),item["count"],join_array(&item["traits"]," or ")));}
        }
        for &i in scenario_goals.union(&current){let c=p.units[i];if c.unlockable&&level<p.minimum_level(c)?{milestones.push(format!("{}: wait until planning level {}; published unlock minimum: {}.",c.name,p.minimum_level(c)?,c.unlock_level));}}
        for &helper in useful.intersection(&current){for &i in &pending{if p.dependencies(&[i].into_iter().collect()).contains(&helper){milestones.push(format!("Field {} to work toward {}: {} Replace after unlock is complete; required upgrades/items/progress still need to be earned.",p.units[helper].name,p.units[i].name,p.units[i].unlock_condition));}}}
        if target.iter().any(|i|p.units[*i].name=="Sona"&&!p.confirmed(p.units[*i],level)){milestones.push(if current.intersection(&p.collab).count()>=3{"Work toward Sona's 15 inspirations with 3 Collab active; this board does not prove any inspirations earned."}else{"Build toward 3 Collab to begin Sona's 15-inspiration task; no progress is assumed."}.into());}
        let late_names:Names=current.iter().copied().chain(target.iter().copied().filter(|_|final_level)).collect();
        for i in late_names{let c=p.units[i];if c.cost==7{milestones.push(format!("{} is quest-only, not a regular shop roll; unlock completion is conditional.",c.name));}if c.name=="Baron Nashor"{milestones.push("Baron consumes 2 board slots. Level 9 is a conservative late-game planning heuristic, not a published unlock minimum (Any).".into());}}
        if level==11{milestones.push("Level 11 requires a special level-cap extension; ordinary leveling is not assumed to reach it.".into());}
        let temporary:Names=current.difference(&target).copied().collect();
        let mut reason="Stable arrival: retain most previous units, add obtainable goals and make small replacements; purposeful off-trait helpers prepare future unlocks.".to_owned();
        if !upgrades.is_empty(){reason.push_str(" Hold this arrival until hits/tasks are ready, then follow the separate buy-before-sell upgrades; the exact end goal is not an instant pivot.");}
        let fallback_names=fallback.as_ref().map(|b|p.set(&b["champions"])).unwrap_or_default();let cap=if level>=8{2}else{1};
        for (label,old,new) in [("Primary",&previous,&current),("No-unlock alternative",&previous_fallback,&fallback_names)]{let removed=old.difference(new).count();if removed>cap{milestones.push(format!("{label}: weighted slots/exclusions force {removed} removals beyond the usual {cap}; no smooth exact-slot completion exists. No empty filler is invented."));}}
        if board.is_none(){reason="No feasible exact-slot board from eligible champions at this level; do not invent a unit or claim the next-level board is available early.".into();}else if !temporary.is_empty(){reason.push_str(" Replace useful temporary setup only after the relevant unlock is complete; shop access is not a purchase guarantee.");}
        let mut stars=fallback.as_ref().and_then(|b|b["planned_stars"].as_object()).cloned().unwrap_or_default();if let Some(board)=&board{if let Some(primary)=board["planned_stars"].as_object(){stars.extend(primary.clone());}}
        steps.push(json!({"level":level,"board":board,"fallback":fallback,"added":p.names(&current.difference(&previous).copied().collect()),"removed":p.names(&previous.difference(&current).copied().collect()),"goal_board":goal,"upgrades":upgrades,"unlock_prep":prep,"temporary_options":temporary_options,"preparation_boards":preparation_boards,"retained":p.names(&current.intersection(&previous).copied().collect()),"temporary":p.names(&temporary),"unlocks":unlocks,"milestones":milestones,"stars":stars,"reason":reason}));
        previous=current;previous_fallback=fallback_names;
    }
    result["buildup"]=json!(steps);result["temporary_slots"]=Value::Object(slots_by_level);
    let mut notes=vec![
        "Conservative planning, not combat, item, gold, rolling or quest-progress simulation. Every unlockable primary unit is conditional on its task.".to_owned(),
        "Primary arrivals preserve previously displayed stars; same-level upgrade phases explicitly plan each increase before any unlock counts it. Planned copies must still be earned.".into(),
        "Planned upgrades are targets, not owned units. Fast-10 rankings apply conditional trait, Senna-preference and reroll-path heuristics, not gold or win-rate estimates.".into(),
        "End-goal locks are not forced at earlier levels. Temporary units are replaced as target units become eligible; exclusions apply throughout.".into(),
        "Fielded members of the selected Final roster are protected, not temporary swap slots. A temporary alternative may add a Final member; it becomes protected once selected.".into(),
        "Ordinary defaults require a nonzero shop chance and conservative rarity timing. Explicit early what-if assumptions can change those estimates; a manually confirmed task can bypass conservative timing only once real ordinary shop odds exist. Published Any is not an ordinary-shop access promise.".into(),
        "Default four-cost acquisition planning starts at level 7 and five-cost at level 9; those are not two-star or ownership claims. Published unlock minima and actual task confirmations remain separate constraints.".into(),
        "No-unlock alternatives exclude every unlockable unit and may not satisfy the final Collab minimum or end-goal locks. None means no exact-slot alternative exists.".into(),
        format!("Shop odds source: TFT Flow Patch 19.1 — {SHOP_SOURCE}"),
        "Primary arrivals normally remove at most one unit per level below 8 and two at 8+. Final upgrades are separate exact-slot conditional exchanges: buy before selling and hold arrival until ready.".into(),
        "Unrelated long unlock chains require an actual Collab native band or featured band. Purposeful dependencies of selected goals are allowed in the guide only; no quest progress is assumed.".into(),
        "Suggested item holders use sourced combat roles and planned-star/rarity heuristics, not a combat or item-inventory simulation. Yone can use AD or AP items as one chosen flex build.".into(),
        "Replacements prefer compatible item roles. Move items after selling an outgoing holder; if it remains fielded, an item remover or a later actual sale is required.".into(),
        "Automatic normal two-star plans: cost 1 at level 4 for keepers/pins, otherwise 5; cost 2 at 6, cost 3 at 7, cost 4 at 8, cost 5 at 10. Quest-only seven-cost units default to one star. Every increase is an explicit earn-and-combine phase.".into(),
        "Reroll context comes from the fixed normal goal, not the viewed board or an unchecked Sona: no Sona goal, a majority of one/two-cost units, or an explicit cheap three-star commitment. Keeper/pinned cost 1/2/3 units can plan three stars at levels 5/6/7; no automatic four-star setup is invented. Manual ranks and early-scenario floors remain explicit assumptions.".into(),
    ];
    if options.no_ad_items && options.no_ap_items {notes.push("NO AD items and NO AP items: offensive item plans are omitted; this is an unitemized, tank and utility alternative.".into());}
    else if options.no_ad_items {notes.push("NO AD items: prefer a magic-damage carry alternative; AD offensive item plans are omitted. Locked units and tank roles remain unchanged.".into());}
    else if options.no_ap_items {notes.push("NO AP items: prefer an attack-damage carry alternative; AP offensive item plans are omitted. Locked units and tank roles remain unchanged.".into());}
    for key in ["purpose","liability"]{let notice=crate::resources::metadata()["legal"][key].as_str().ok_or_else(||format!("Missing legal {key} metadata."))?;notes.push(notice.to_owned());}
    for &i in locked.intersection(&default_blocked){notes.push(format!("Manual chain lock: {} overrides the default unrelated-chain restriction. Complete its task and dependencies; no acquired copies, stars, items or progress are assumed.",p.units[i].name));}
    if options.size==11{notes.push("Level 11 requires a special level-cap extension, not ordinary leveling.".into());}
    if !options.early_availability.is_empty(){notes.push("Static pre-game what-if scenario only: early availability and planned stars are assumptions, not owned copies, earned ranks or completed unlock tasks. Published level requirements and real setup/check phases still apply.".into());}
    if options.current_level>0||!options.completed_unlocks.is_empty(){
        notes.push("Manual unlock tracker: a confirmation records only the sourced task and its level, never owned copies, items, stars or completed Collab cashout. Earlier boards do not receive retroactive unlock access; published minima and real ordinary shop odds remain required.".into());
    }
    if !choices.is_empty(){notes.push("Selected slot alternatives remain pinned through later levels. Unpin to change a conflicting setup; the exact final roster is restored only by an explicit buy-before-sell final pivot.".into());}
    result["buildup_notes"]=json!(notes);
    enforce_path(&p,breadth.as_ref().unwrap_or(&p),&mut result,&target,&excluded,&blocked,choices,resume)?;
    guidance::annotate_result(&mut result,data)?;
    trait_support::annotate_trait_support(&mut result,data)?;
    positioning::annotate_positioning(&mut result,data)?;
    for step in result["buildup"].as_array_mut().unwrap(){
        for index in 0..step["preparation_boards"].as_array().unwrap().len(){
            let setup=&step["preparation_boards"][index];
            if setup["scheduled"]==true{
                let phase=setup["scheduled_phase"].as_u64().unwrap() as usize;
                let board=if phase==0{step["board"].clone()}else{step["upgrades"][phase-1]["board"].clone()};
                step["preparation_boards"][index]["board"]=board;
            }
        }
    }
    if let Some(resume)=resume{
        for n in 0..resume.level-1{result["buildup"][n]=resume.source["buildup"][n].clone();}
        result["buildup"][resume.level-1]["board"]=resume.source["buildup"][resume.level-1]["board"].clone();
        for n in 0..resume.phase{result["buildup"][resume.level-1]["upgrades"][n]=resume.source["buildup"][resume.level-1]["upgrades"][n].clone();}
    }
    Ok(result)
}

fn text<'a>(v:&'a Value,key:&str)->&'a str{v[key].as_str().unwrap_or("")}
fn join_array(v:&Value,separator:&str)->String{v.as_array().into_iter().flatten().filter_map(Value::as_str).collect::<Vec<_>>().join(separator)}

fn boolean(value:bool)->f64 {if value{1.0}else{0.0}}

fn format_roster(lines:&mut Vec<String>,label:&str,board:&Value,data:&GameData) {
    if board.is_null(){lines.push(format!("{label}: no feasible exact-slot board."));return}
    let names=board["champions"].as_array().map(Vec::as_slice).unwrap_or(&[]);
    let roster=names.iter().filter_map(Value::as_str).map(|name|{
        let rank=board["planned_stars"][name].as_u64().unwrap_or(1) as usize;
        let conditional=if data.champions.get(name).is_some_and(|c|c.unlockable){", conditional unlock"}else{""};
        format!("{name} ({}{conditional})","★".repeat(rank))
    }).collect::<Vec<_>>().join(", ");
    lines.push(format!("{label}: {roster}"));
    let traits=board["traits"].as_array().into_iter().flatten().map(|row|format!("{} {} (tier {})",text(row,"name"),row["count"],row["tier"])).collect::<Vec<_>>().join(", ");
    lines.push(format!("Traits: {}",if traits.is_empty(){"none"}else{&traits}));
    let holders=[("tank","Tank"),("ad","AD carry"),("ap","AP carry"),("flex","AD/AP flex — choose one build")].iter().map(|(kind,label)|{
        let holder=board["item_holders"][*kind].as_str().filter(|s|!s.is_empty()).unwrap_or(if ["ad","ap"].contains(kind){"no dedicated holder"}else{"no suitable holder"});
        format!("{label}: {holder}")
    }).collect::<Vec<_>>().join("; ");
    lines.push(format!("Suggested item holders (if those items are available): {holders}"));
    for build in board["planned_item_builds"].as_array().into_iter().flatten(){
        let holder=build["holder"].as_str().filter(|s|!s.is_empty()).unwrap_or("unassigned — no suitable holder");
        lines.push(format!("Planned {} items for {} on {holder}: {} [{}]",text(build,"kind").to_uppercase(),text(build,"target"),join_array(&build["items"],", "),text(build,"label")));
        lines.push(format!("  {}",text(build,"note")));lines.push(format!("  Item plan source: {}",text(build,"source")));
    }
    let support=board["champion_trait_support"].as_object().into_iter().flat_map(|m|m.iter()).map(|(name,support)|{
        let percent=support["percent"].as_f64().map_or_else(||"n/a".into(),|p|format!("{p}%"));format!("{name}: {percent}")
    }).collect::<Vec<_>>().join("; ");
    lines.push(format!("Personal trait-tier activation (not damage efficiency): {support}"));
    if let Some(anchor)=board["frontline_anchor"].as_str().filter(|s|!s.is_empty()){lines.push(format!("Frontline anchor: {anchor}"));}
    for note in board["strategy_notes"].as_array().into_iter().flatten().filter_map(Value::as_str){lines.push(format!("Strategy: {note}"));}
}
fn format_task(lines:&mut Vec<String>,task:&Value,label:&str){
    lines.push(format!("{label} {} [{}]: {}",text(task,"name"),text(task,"level"),text(task,"condition")));
    if task["helpers"].as_array().is_some_and(|a|!a.is_empty()){lines.push(format!("  Fielded setup helpers: {}",join_array(&task["helpers"],", ")));}
    if task["count_mode"]=="distinct_champions"{lines.push(format!("  Requires {} distinct champions from {}; stars do not replace distinct units.",task["count"],join_array(&task["traits"]," or ")));}
    if !text(task,"source").is_empty(){lines.push(format!("  Source: {}",text(task,"source")));}
    if !text(task,"note").is_empty(){lines.push(format!("  {}",text(task,"note")));}
}
fn format_changes(lines:&mut Vec<String>,phase:&Value){
    for replacement in phase["replacements"].as_array().into_iter().flatten(){
        let label=if replacement["same_role"].as_bool().unwrap_or(false){format!("role match: {}",text(replacement,"profile"))}else if replacement["profile"]=="slot_exchange"{"weighted slot exchange".into()}else{"roster replacement — different item roles".into()};
        lines.push(format!("Replace {} → {} ({label}).",join_array(&replacement["outgoing"],", "),join_array(&replacement["incoming"],", ")));
    }
    for transfer in phase["item_transfers"].as_array().into_iter().flatten(){
        let method=if transfer["method"]=="sell_outgoing"{"buy the incoming unit, then sell the outgoing holder to release its items"}else{"the old holder stays fielded: use an item remover or wait until you actually sell it"};
        lines.push(format!("{} items: {} → {}; {method}. {}",text(transfer,"kind").to_uppercase(),text(transfer,"outgoing"),text(transfer,"incoming"),text(transfer,"condition")));
    }
    for upgrade in phase["star_upgrades"].as_array().into_iter().flatten(){lines.push(format!("Plan star upgrade: {} {} → {} stars; earn and combine the copies first.",text(upgrade,"champion"),upgrade["from"],upgrade["to"]));}
    for check in phase["unlock_checks"].as_array().into_iter().flatten(){format_task(lines,check,"CHECK BEFORE BUYING —");}
    for replacement in phase["copy_replacements"].as_array().into_iter().flatten(){lines.push(format!("Buy fresh {} at {} stars before selling the {}-star preparation copy.",text(replacement,"champion"),replacement["to"],replacement["from"]));}
}
pub fn format_buildup(result:&Value,data:&GameData)->Result<String,String>{
    if !result.is_object()||!result["champions"].is_array(){return Err("A recommendation with champions is required.".into())}
    let mut lines=Vec::new();format_roster(&mut lines,"Exact end goal (not the leveling arrival)",result,data);
    for (name,assumption) in result["early_availability"].as_object().into_iter().flatten(){
        lines.push(format!("Early availability assumption: {name}, from level {}, planned {} stars (static pre-game what-if; not owned copies or completed unlock progress).",assumption["level"],assumption["stars"]));
    }
    lines.push(format!("Manually tracked current level: {} (0 means static planning).",result["current_level"].as_u64().unwrap_or(0)));
    for (name,level) in result["completed_unlocks"].as_object().into_iter().flatten(){
        lines.push(format!("User-confirmed unlock: {name}, completed at level {level}; task only, not purchased copies or earned stars."));
        if let Some(c)=data.champions.get(name){lines.push(format!("  Task: {} Source: {}",c.unlock_condition,c.unlock_source));}
    }
    for note in result["buildup_notes"].as_array().into_iter().flatten().filter_map(Value::as_str){lines.push(format!("Note: {note}"));}
    for step in result["buildup"].as_array().into_iter().flatten(){
        lines.push(format!("\nLevel {}",step["level"]));
        if !step["pivot_warning"].is_null(){
            lines.push(format!("FAST-MICRO PIVOT WARNING: {}",text(&step["pivot_warning"],"reason")));
            lines.push(format!("  Secure additions: {}; outgoing setup: {}.",join_array(&step["pivot_warning"]["added"],", "),join_array(&step["pivot_warning"]["removed"],", ")));
            for instruction in step["pivot_warning"]["sequence"].as_array().into_iter().flatten().filter_map(Value::as_str){lines.push(format!("  Sequence: {instruction}"));}
        }
        for preparation in step["preparation_boards"].as_array().into_iter().flatten(){
            format_task(&mut lines,preparation,"PREPARE BEFORE ARRIVAL —");lines.push(format!("  Setup status: {}",text(preparation,"status")));
            format_roster(&mut lines,&format!("  Actual unlock setup for {}",text(preparation,"target")),&preparation["board"],data);
            lines.push(format!("  Requirements: {}",text(preparation,"requirements")));
            if !text(preparation,"positioning_requirement").is_empty(){lines.push(format!("  Position requirement: {}",text(preparation,"positioning_requirement")));}
            for dependency in preparation["conditional_dependencies"].as_array().into_iter().flatten(){format_task(&mut lines,dependency,"  First unlock setup dependency");}
        }
        for option in step["temporary_options"].as_array().into_iter().flatten(){lines.push(format!("Temporary choice {}: {} (options: {})",text(option,"slot"),text(option,"selected"),join_array(&option["options"],", ")));}
        format_roster(&mut lines,"Primary arrival",&step["board"],data);format_changes(&mut lines,step);
        for (label,key) in [("Add","added"),("Remove","removed"),("Keep","retained"),("Temporary","temporary")]{let names=join_array(&step[key],", ");lines.push(format!("{label}: {}",if names.is_empty(){"none"}else{&names}));}
        for task in step["unlocks"].as_array().into_iter().flatten(){format_task(&mut lines,task,"Conditional");}
        for task in step["unlock_prep"].as_array().into_iter().flatten(){format_task(&mut lines,task,"Future unlock prep for");}
        format_roster(&mut lines,"No-unlock alternative",&step["fallback"],data);
        for milestone in step["milestones"].as_array().into_iter().flatten().filter_map(Value::as_str){lines.push(format!("Milestone: {milestone}"));}
        lines.push(format!("Reason: {}",text(step,"reason")));
        if !step["goal_board"].is_null(){format_roster(&mut lines,"Exact final-level goal after upgrades",&step["goal_board"],data);}
        for (i,phase) in step["upgrades"].as_array().into_iter().flatten().enumerate(){
            format_roster(&mut lines,&format!("Upgrade phase {} — buy before selling",i+1),&phase["board"],data);format_changes(&mut lines,phase);
            for (label,key) in [("Buy/add","added"),("Then sell/remove","removed"),("Keep","retained")]{let names=join_array(&phase[key],", ");lines.push(format!("{label}: {}",if names.is_empty(){"none"}else{&names}));}
            for task in phase["unlocks"].as_array().into_iter().flatten(){format_task(&mut lines,task,"Conditional");}
            for milestone in phase["milestones"].as_array().into_iter().flatten().filter_map(Value::as_str){lines.push(format!("Milestone: {milestone}"));}
            lines.push(format!("Reason: {}",text(phase,"reason")));
        }
    }
    Ok(lines.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn data(traits:Value,champions:Value)->GameData {GameData::from_value(json!({"traits":traits,"champions":champions})).unwrap()}
    fn options(size:usize,mode:&str)->OptimizeOptions {OptimizeOptions::from_value(&json!({"size":size,"mode":mode,"min_collab":0,"limit":1})).unwrap()}
    fn ids(p:&Planner,names:&[&str])->Names {names.iter().map(|n|p.ids[*n]).collect()}
    #[test]
    fn selecting_a_final_member_hides_its_control_but_preserves_canonical_history(){
        let d=data(json!([{"name":"Band","breakpoints":[2],"unique":false}]),json!([
            {"name":"Final","cost":1,"traits":["Band"],"role":"Magic Tank"},
            {"name":"Temporary","cost":1,"traits":["Band"],"role":"Magic Tank"},
            {"name":"Alternative","cost":1,"traits":["Band"],"role":"Magic Tank"}]));
        let o=options(1,"balanced");let p=Planner::new(&d,&o,false).unwrap();
        let target=ids(&p,&["Final"]);let empty=Names::new();let slots=vec!["Temporary".to_owned()];
        let board=p.evaluate(1,&ids(&p,&["Temporary"])).unwrap();
        let select=|board,choices:&Map<String,Value>,pins:&BTreeMap<String,String>|temporary_selections(
            &p,1,Some(board),None,&target,&empty,&empty,&empty,&empty,&BTreeMap::new(),&slots,choices,pins);
        let (_,offered)=select(board.clone(),&Map::new(),&BTreeMap::new()).unwrap();
        assert!(offered[0]["options"].as_array().unwrap().iter().any(|name|name=="Final"));
        let choices=Map::from_iter([("Temporary".to_owned(),json!("Final"))]);
        let (selected,hidden)=select(board,&choices,&BTreeMap::new()).unwrap();
        assert!(hidden.is_empty());assert_eq!(p.set(&selected.as_ref().unwrap()["champions"]),target);
        let pins=BTreeMap::from([("Temporary".to_owned(),"Final".to_owned())]);
        let (replayed,hidden)=select(selected.clone().unwrap(),&choices,&pins).unwrap();
        assert_eq!(replayed,selected);assert!(hidden.is_empty());
        let forged=Map::from_iter([("Temporary".to_owned(),json!("Alternative"))]);
        assert!(select(selected.unwrap(),&forged,&pins).unwrap_err().contains("selected Final roster are protected"));
    }
    #[test]
    fn complete_catalog_has_sourced_recipes_and_only_true_manual_families(){
        let d=crate::resources::bundled_data();let o=options(10,"balanced");let p=Planner::new(d,&o,false).unwrap();
        let target=(0..p.units.len()).collect();let recipes=recipes(&p,&target,&Names::new());
        assert_eq!(recipes.len(),32);
        let manual:Vec<_>=recipes.iter().filter(|(_,r)|r.kind=="manual").map(|(i,_)|p.units[*i].name.as_str()).collect();
        assert_eq!(manual,vec!["Pyke","Rakan","Seraphine"]);
        for (i,r) in &recipes {assert_eq!(r.requirements,p.units[*i].unlock_condition);assert!(!p.units[*i].unlock_source.is_empty());}
        for (name,kind) in [("Thresh","active"),("Smeech","many_traits"),("Vladimir","position"),("Yasuo","stars"),("Mordekaiser","stars"),("Taliyah","costs"),("Alistar","role"),("Ashe","holder"),("Tibbers","named"),("Baron Nashor","named"),("Evelynn","distinct")] {assert_eq!(recipes[&p.ids[name]].kind,kind);}
    }
    #[test]
    fn retention_is_a_hard_search_feasibility_dimension(){
        let champions:Vec<_>=(0..18).map(|i|json!({"name":format!("Unit{i:02}"),"cost":1,"traits":["Padding"]})).collect();
        let d=data(json!([{"name":"Padding","breakpoints":[99],"unique":false}]),json!(champions));let o=options(10,"balanced");let p=Planner::new(&d,&o,false).unwrap();
        let previous:Names=(0..7).collect();let weights:Vec<_>=(0..18).map(|i|if previous.contains(&i){0.0}else{100000.0}).collect();
        for level in [7,8] {for pool in [(0..18).collect::<Vec<_>>(),(0..18).rev().collect()] {let answer=p.search(level,&pool,&weights,false,&previous,8).unwrap();assert!(answer.intersection(&previous).count()>=if level==7{6}else{5});}}
    }
    #[test]
    fn completed_boards_never_receive_unearned_progress_merit(){
        let d=data(json!([{"name":"Near","breakpoints":[3],"unique":false},{"name":"Padding","breakpoints":[99],"unique":false}]),json!([{"name":"Fixed","cost":1,"traits":["Near"]},{"name":"A Ordinary","cost":1,"traits":["Padding"]},{"name":"Z Near","cost":1,"traits":["Near"]}]));
        let o=options(2,"synergies");let p=Planner::new(&d,&o,false).unwrap();let mut weights=vec![0.0;3];weights[p.ids["Fixed"]]=1000.0;
        for pool in [(0..3).collect::<Vec<_>>(),(0..3).rev().collect()] {assert_eq!(p.search(2,&pool,&weights,false,&Names::new(),8).unwrap(),ids(&p,&["A Ordinary","Fixed"]));}
    }
    #[test]
    fn jazz_bridge_requires_fielded_native_link_and_active_jazz(){
        let d=data(json!([{"name":"Collab","breakpoints":[2],"unique":false},{"name":"Jazz","breakpoints":[2,3],"unique":false},{"name":"Link","breakpoints":[2],"unique":false},{"name":"Choice","breakpoints":[1],"unique":false}]),json!([{"name":"Core Jazz","cost":1,"traits":["Jazz","Link"]},{"name":"Core Collab","cost":1,"traits":["Link"]},{"name":"Offboard Collab","cost":1,"traits":["Link"]},{"name":"A Ordinary","cost":1,"traits":["Choice"]},{"name":"Z Jazz","cost":1,"traits":["Jazz"]}]));
        for mode in ["balanced","high_value","synergies"] {for collab in ["Core Collab","Offboard Collab"] {let mut o=options(3,mode);o.collab=vec![collab.into()];let p=Planner::new(&d,&o,false).unwrap();let mut weights=vec![0.0;5];weights[p.ids["Core Jazz"]]=1000.0;weights[p.ids["Core Collab"]]=1000.0;weights[p.ids["A Ordinary"]]=30.0;
            let pool=ids(&p,&["Core Jazz","Core Collab","A Ordinary","Z Jazz"]).into_iter().collect::<Vec<_>>();let qualified=collab=="Core Collab"&&mode!="synergies";
            let expected=ids(&p,&["Core Jazz","Core Collab",if qualified{"Z Jazz"}else{"A Ordinary"}]);assert_eq!(p.search(3,&pool,&weights,false,&Names::new(),8).unwrap(),expected);
        }}
    }
    #[test]
    fn forty_four_star_counts_have_full_stage_tables(){
        let champions:Vec<_>=(0..11).map(|i|json!({"name":format!("K{i}"),"cost":1,"traits":["K/DA"]})).collect();
        let d=data(json!([{"name":"K/DA","breakpoints":[12,24,44],"unique":false,"count_mode":"stars"}]),json!(champions));let mut o=options(11,"balanced");o.stars=d.champions.keys().map(|n|(n.clone(),4)).collect();let p=Planner::new(&d,&o,false).unwrap();let target=(0..11).collect();let board=p.board(11,&target,&Names::new(),&Names::new(),&Names::new(),false).unwrap().unwrap();assert_eq!(board["traits"][0]["count"],44);assert_eq!(board["traits"][0]["tier"],44);
    }
    #[test]
    fn weighted_upgrade_is_a_complete_buy_before_sell_exchange(){
        let d=data(json!([{"name":"Padding","breakpoints":[99],"unique":false}]),json!([{"name":"Starter 0","cost":1,"traits":["Padding"]},{"name":"Starter 1","cost":1,"traits":["Padding"]},{"name":"Starter 2","cost":1,"traits":["Padding"]},{"name":"Weighted","cost":1,"slots":3,"traits":["Padding"]}]));
        let o=options(3,"balanced");let p=Planner::new(&d,&o,false).unwrap();let arrival=p.evaluate(3,&ids(&p,&["Starter 0","Starter 1","Starter 2"])).unwrap();let goal=p.evaluate(3,&ids(&p,&["Weighted"])).unwrap();let phases=upgrade_phases(&p,3,Some(&arrival),&goal).unwrap();assert_eq!(phases.len(),1);assert_eq!(phases[0]["board"],goal);assert_eq!(phases[0]["removed"].as_array().unwrap().len(),3);assert!(phases[0]["milestones"].as_array().unwrap().iter().any(|n|n.as_str().unwrap().contains("larger simultaneous exchange")));
    }
    #[test]
    fn named_dependencies_are_transitive_and_negative_requirements_are_not_helpers(){
        let d=data(json!([{"name":"Band","breakpoints":[2],"unique":false}]),json!([{"name":"Seed","cost":1,"traits":["Band"]},{"name":"Helper","cost":2,"traits":["Band"],"unlockable":true,"unlock_condition":"Have Seed holding 1 item.","unlock_level":"Any","unlock_source":"https://example.test"},{"name":"Goal","cost":3,"traits":["Band"],"unlockable":true,"unlock_condition":"Field Helper without Forbidden unlocked.","unlock_level":"Any","unlock_source":"https://example.test"},{"name":"Forbidden","cost":1,"traits":["Band"]}]));
        let o=options(7,"balanced");let p=Planner::new(&d,&o,false).unwrap();assert_eq!(p.dependencies(&ids(&p,&["Goal"])),ids(&p,&["Helper","Seed"]));
    }
    #[test]
    fn preparation_helpers_preserve_candidate_preference_and_cost_order(){
        let d=crate::resources::bundled_data();let o=options(10,"balanced");let p=Planner::new(d,&o,false).unwrap();
        let target=ids(&p,&["Thresh","Yasuo","Taliyah"]);let recipes=recipes(&p,&target,&Names::new());
        let thresh=p.ids["Thresh"];let preferred=ids(&p,&["Renata Glasc"]);
        let chosen=preparation(&p,&p,3,thresh,&recipes[&thresh],&target,&Names::new(),&preferred,&Names::new()).unwrap();
        assert_eq!(chosen["helpers"],json!(["Renata Glasc","Katarina"]));
        let default=preparation(&p,&p,3,thresh,&recipes[&thresh],&target,&Names::new(),&Names::new(),&Names::new()).unwrap();
        assert_eq!(default["helpers"],json!(["Katarina","Nasus"]));
        let yasuo=p.ids["Yasuo"];let recipe=&recipes[&yasuo];
        let stars=preparation(&p,&p,7,yasuo,recipe,&target,&Names::new(),&Names::new(),&Names::new()).unwrap();
        assert!(!p.set(&stars["board"]["champions"]).contains(&yasuo));
        assert!(p.set(&stars["helpers"]).iter().map(|i|stars["board"]["planned_stars"][&p.units[*i].name].as_u64().unwrap()).sum::<u64>()>=recipe.count as u64);
        let taliyah=p.ids["Taliyah"];
        let costs=preparation(&p,&p,10,taliyah,&recipes[&taliyah],&target,&Names::new(),&Names::new(),&Names::new()).unwrap();
        let actual:Vec<_>=costs["helpers"].as_array().unwrap().iter().take(5).map(|name|d.champions[name.as_str().unwrap()].cost).collect();
        assert_eq!(actual,vec![1,2,3,4,5]);
    }
}
