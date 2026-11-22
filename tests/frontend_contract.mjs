// Runs the actual frontend API/auth files against an isolated local backend.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import vm from 'node:vm';

const [frontend, base] = process.argv.slice(2);
const storage = new Map();
const cookies = new Map();
const realFetch = globalThis.fetch;
// Simulate the browser storage and location surfaces used by the frontend scripts.
globalThis.window = {
    VIRASAT_API_BASE_URL: base,
    location: {hostname: 'localhost', assign(url) {this.next = url;}},
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,String(value)), removeItem: key => storage.delete(key)}
};
/** Keep session cookies and the local frontend origin across contract requests. */
globalThis.fetch = async (url, options = {}) => {
    const headers = new Headers(options.headers);
    headers.set('Origin', 'http://localhost:5173');
    if (cookies.size) headers.set('Cookie', [...cookies].map(([k,v])=>k+'='+v).join('; '));
    const response = await realFetch(url, {...options, headers, redirect:'manual'});
    for (const value of response.headers.getSetCookie()) {
        const pair = value.split(';')[0];
        const at = pair.indexOf('=');
        const key = pair.slice(0,at), content = pair.slice(at+1);
        if (!content || /Max-Age=0/i.test(value)) cookies.delete(key);
        else cookies.set(key, content);
    }
    return response;
};
for (const file of ['src/api.js','src/auth.js']) vm.runInThisContext(readFileSync(join(frontend,file),'utf8'), {filename:file});
const api = window.VirasatAPI, auth = window.VirasatAuth;
/** Report a completed frontend flow after its assertions pass. */
const passed = name => console.log('PASS frontend wrappers:',name);
const monuments = await api.monuments({search:'Taj',city:'Agra'});
assert.equal(monuments[0].id,1);
assert.equal((await api.monument(1)).image_url,'https://example.org/taj.jpg');
assert.equal(await auth.currentUser(),null);
passed('guest discovery, search, detail and image normalization');

await auth.continueGoogle();
assert.equal(window.location.next,base+'/auth/google/continue');
assert.equal((await fetch(window.location.next)).status,302);
assert.equal((await fetch(base+'/auth/google/callback?code=fixture')).status,303);
const before = await auth.currentUser();
assert.equal(before.username,'google:contract-subject');
assert.equal(before.credentials_set,false);
assert.equal(before.login_username,null);
passed('Continue with Google, callback cookie and setup prompt fields (test OAuth provider)');

const story = await api.story(1);
assert.ok(story.story);
assert.equal((await api.audio(1)).headers.get('content-type'),'audio/mpeg');
const session = await api.createSession(1);
for (let i=0;i<4;i++) await api.askQuestion(session.session_id,'Who commissioned it? '+i,crypto.randomUUID());
const requestId = crypto.randomUUID();
const answer = await api.askQuestion(session.session_id,'Who commissioned it?',requestId);
assert.ok(answer.supported && answer.evidence[0].text);
assert.deepEqual(await api.askQuestion(session.session_id,'Who commissioned it?',requestId),answer);
assert.equal((await api.getSession(session.session_id)).history.length,3);
// Reuse a request ID to simulate simultaneous retries of the same question.
const concurrentId = crypto.randomUUID();
const simultaneous = await Promise.allSettled([
    api.askQuestion(session.session_id,'Who commissioned this monument?',concurrentId),
    api.askQuestion(session.session_id,'Who commissioned this monument?',concurrentId)
]);
assert.ok(simultaneous.some(result=>result.status==='fulfilled'));
for (const result of simultaneous) if (result.status==='rejected') assert.equal(result.reason.status,409);
await api.askQuestion(session.session_id,'Who commissioned this monument?',concurrentId);
assert.equal((await api.getSession(session.session_id)).history.filter(a=>a.request_id===concurrentId).length,1);
const unknown = await api.askQuestion(session.session_id,'What is the weather?',crypto.randomUUID());
assert.equal(unknown.supported,false);
assert.deepEqual(unknown.evidence,[]);
passed('story/audio contracts, private session, last-three grounded answers and retry IDs');

const capabilities = await api.capabilities();
assert.equal(capabilities.transport_search,false);
assert.equal(capabilities.hotel_search,false);
assert.equal(capabilities.profile_onboarding,true);
assert.equal((await api.suggestPlaces('jaip'))[0].label,'Jaipur, Rajasthan, India');
const start = new Date(Date.now()+30*86400000).toISOString().slice(0,10);
const end = new Date(Date.now()+31*86400000).toISOString().slice(0,10);
const stops = ['London','Paris','Rome','Tokyo','Singapore','Delhi'].map(label=>({label}));
const transport = await api.transportSearch({origin:{label:'Bengaluru'},destination:{label:'Agra'},stops,departure_date:start,return_date:null,travelers:2,modes:['flight','train'],currency:'INR'});
assert.equal(transport.status,'unavailable');
assert.deepEqual(transport.results,[]);
const hotels = await api.hotelSearch({destination:'Agra',check_in:start,check_out:end,travelers:2,rooms:1,star_ratings:[3,4],currency:'INR'});
assert.equal(hotels.status,'unavailable');
passed('capabilities, autocomplete, six-stop transport and honest unavailable hotels');

const input = {destination:'Agra',days:1,travelers:2,budget:1000,currency:'INR',interests:['heritage'],city:'Agra',area:'Taj Ganj',location:{latitude:27.1751,longitude:78.0421},origin:{label:'Bengaluru'},stops,departure_date:start,return_date:end,travel_mode:'flight',hotel_preferences:{star_ratings:[3,4],rooms:1}};
const plan = await api.createPlan(input);
assert.ok(plan.plan_id && plan.created_at && plan.title && plan.summary);
assert.equal(plan.request.stops.length,6);
assert.equal(plan.estimated_total,700);
assert.equal(plan.days[0].estimated_total,700);
assert.equal(plan.budget_status,'within_budget');
assert.ok(plan.sources.length && plan.warnings.length && plan.tips.length);
assert.deepEqual(await api.getPlan(plan.plan_id),plan);
assert.ok((await (await api.pdf(plan.plan_id)).text()).startsWith('%PDF'));
passed('extended itinerary, server totals, saved response and PDF download');

// Credential setup must keep the established Google account and its saved records.
const profile = await auth.updateProfile({full_name:'Actual Submitted Name',gender:'prefer_not_to_say'});
assert.equal(profile.date_of_birth,null);
assert.equal(profile.onboarding_completed,true);
assert.equal(profile.credentials_set,false);
const configured = await auth.setCredentials('Traveler.One','local-contract-password');
assert.equal(configured._id,before._id);
assert.equal(configured.username,before.username);
assert.equal(configured.login_username,'Traveler.One');
assert.equal(configured.credentials_set,true);
assert.equal(configured.onboarding_completed,true);
await assert.rejects(()=>auth.setCredentials('Different','replacement-password'));
await assert.rejects(()=>auth.login('traveler.one','wrong-password'));
const login = await auth.login('TRAVELER.ONE','local-contract-password');
assert.ok(login.access_token);
assert.equal((await auth.currentUser())._id,before._id);
assert.deepEqual(await api.getPlan(plan.plan_id),plan);
assert.equal((await api.getSession(session.session_id)).monument_id,1);
passed('optional DOB, Google-linked credentials, password login and unchanged resource ownership');

await auth.logout();
assert.equal(await auth.currentUser(),null);
await auth.register('Other.Traveler','other-contract-password');
await auth.login('other.traveler','other-contract-password');
await assert.rejects(()=>api.getSession(session.session_id),error=>error.status===404);
await assert.rejects(()=>api.getPlan(plan.plan_id),error=>error.status===404);
await assert.rejects(()=>api.pdf(plan.plan_id),error=>error.status===404);
passed('logout revocation, local registration and cross-user session/plan/PDF privacy');
console.log('All actual frontend API/auth wrapper checks passed.');
