'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const review=require('../scripts/season_projection_review.cjs');
global.window=global;
require('../web/season-engine.js');

function fixture(){
 const players={};
 // Six 40-minute incumbents consume 240 minutes. The seventh is stronger,
 // but receives no minutes until an actual Out player frees the budget.
 for(let id=1;id<=6;id++)players[id]={id,n:`Incumbent ${id}`,team:'A',MPG:40,bpm:1};
 players[7]={id:7,n:'Strong omitted',team:'A',MPG:25,bpm:7};
 return {players};
}

test('Default healthy scenario retains the stronger omitted player hold',()=>{
 const reason=review(fixture(),NBAI_SEASON,'A');
 assert.match(reason,/Strong omitted/);
 assert.match(reason,/no scenario minutes/);
 assert.doesNotMatch(reason,/healthy-scenario/);
});

test('The same Out set frees actual engine minutes and removes a false hold',()=>{
 const data=fixture(),out=new Set(['1']);
 const roster=NBAI_SEASON.rotation(data,'A',NBAI_SEASON.assignments(data),out);
 assert(!roster.some(p=>p.id===1));
 assert(roster.find(p=>p.id===7).min>0);
 assert.equal(review(data,NBAI_SEASON,'A',out),null);
 assert.deepEqual([...out],['1']);
});

test('An omitted stronger player who is Out cannot trigger the review',()=>{
 assert.equal(review(fixture(),NBAI_SEASON,'A',new Set(['7'])),null);
});

test('Another stronger eligible omitted player still triggers the hold',()=>{
 const data=fixture();
 data.players[8]={id:8,n:'Second omitted',team:'A',MPG:24,bpm:5};
 const reason=review(data,NBAI_SEASON,'A',new Set(['7']));
 assert.match(reason,/Second omitted/);
 assert.doesNotMatch(reason,/Strong omitted/);
});

test('Unrelated Out IDs do not excuse eligible stronger omissions',()=>{
 assert.match(review(fixture(),NBAI_SEASON,'A',new Set(['999'])),/Strong omitted/);
});

test('Unknown participation is still eligible and cannot silently clear a hold',()=>{
 const data=fixture();data.players[7].status='Unknown';
 assert.match(review(data,NBAI_SEASON,'A',new Set()),/Strong omitted/);
});

test('Other teams and low-minute players retain the existing review scope',()=>{
 const data=fixture();data.players[7].MPG=19;
 data.players[8]={id:8,n:'Other team',team:'B',MPG:35,bpm:20};
 assert.equal(review(data,NBAI_SEASON,'A'),null);
});
