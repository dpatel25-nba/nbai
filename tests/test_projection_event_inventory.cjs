'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const inventory=require('../scripts/projection_event_inventory.cjs');
const data={teams:{BOS:{name:'Boston Celtics'},BKN:{name:'Brooklyn Nets'}},schedule:[{id:'nba1',home:'BOS',away:'BKN',tip:'2026-10-27T23:30:00Z'}]};
const entry={eventId:'espn1',home:'BOS',away:'BKN',startsAt:'2026-10-27T23:30:00Z'};
test('public schedule supplies missing exact game identities without quotes',()=>{
 const events=inventory(data,{events:[]},{events:[entry,entry]});
 assert.equal(events.length,1);assert.equal(events[0].id,'espn:espn1');
 assert.equal(events[0].identitySource,'espn-availability-schedule');
 assert.equal(events[0].bookmakers,undefined);
});
test('existing sportsbook game stays unique',()=>{
 const event={id:'odds1',sport_key:'basketball_nba',home_team:'Boston Celtics',away_team:'Brooklyn Nets',commence_time:entry.startsAt};
 assert.deepEqual(inventory(data,{events:[event]},{events:[entry]}),[event]);
});
test('wrong day, venue, unknown teams and provisional games cannot add identities',()=>{
 const entries=[{...entry,startsAt:'2026-10-28T23:30:00Z'},{...entry,home:'BKN',away:'BOS'},{...entry,home:'XXX'}];
 assert.deepEqual(inventory(data,{events:[]},{events:entries}),[]);
 assert.deepEqual(inventory({...data,schedule:[{...data.schedule[0],provisional:true}]},{events:[]},{events:[entry]}),[]);
});
