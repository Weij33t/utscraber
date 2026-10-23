import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyInput, stamp, cleanLibrary } from '../web/core.js';

test('recognises YouTube video links and rejects lookalike domains',()=>{
  for(const value of ['dQw4w9WgXcQ','https://youtu.be/dQw4w9WgXcQ?t=20','youtube.com/watch?v=dQw4w9WgXcQ&list=abc','https://m.youtube.com/shorts/dQw4w9WgXcQ','https://www.youtube.com/live/dQw4w9WgXcQ'])assert.deepEqual(classifyInput(value),{type:'video',value:'dQw4w9WgXcQ'});
  for(const value of ['https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ','https://example.com','https://youtube.com/watch?v=invalid'])assert.equal(classifyInput(value).type,'invalid');
  assert.equal(classifyInput('Jerry CS').type,'search');
  assert.equal(classifyInput('CS на русском').type,'search');
});
test('recognises channels including Cyrillic handles',()=>{
  for(const input of ['@jerry','@пример','https://youtube.com/@jerry/videos','https://www.youtube.com/channel/UC1234567890123456789012'])assert.equal(classifyInput(input).type,'channel');
});
test('formats timestamps spanning hours',()=>{
  assert.equal(stamp(0),'0:00'); assert.equal(stamp(65.8),'1:05'); assert.equal(stamp(3661),'1:01:01'); assert.equal(stamp(-2),'0:00');
});
test('import drops invalid IDs and executable channel URLs, preserves offline text',()=>{
  const data=cleanLibrary({videos:[{id:'bad',title:'Bad',channel:'x'},{id:'dQw4w9WgXcQ',title:'Hello',channel:'Author',channelUrl:'javascript:alert(1)',transcript:{language:'en',languages:[{code:'en',name:'English'}],segments:[{start:2,text:'Hello'}]}}],channels:[{id:'x',title:'x',url:'javascript:alert(1)'}]});
  assert.equal(data.videos.length,1);assert.equal(data.channels.length,0);assert.equal(data.videos[0].channelUrl,undefined);assert.equal(data.videos[0].transcript.segments[0].text,'Hello');assert.doesNotThrow(()=>JSON.stringify(data));
  assert.equal(cleanLibrary(null),null);
});
