"""Simulated adapter checks. Fixture offers are never production inventory."""

import asyncio
import copy
from datetime import datetime,timedelta,timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from virasatguide_backend.main import app
from virasatguide_backend.data.database import get_database,get_users_collection
from virasatguide_backend.routes.auth import get_current_user
from virasatguide_backend.travel import planner,search_providers,capabilities
from virasatguide_backend.travel.search_models import TransportOption,HotelOption
from test_travel import Collection,Monuments,draft

START=(datetime.now(timezone.utc)+timedelta(days=30)).date()
END=START+timedelta(days=5)
JOURNEY={'origin':{'label':'Bengaluru, India'},'destination':{'label':'Agra'},'stops':[],
         'departure_date':START.isoformat(),'return_date':END.isoformat(),'travelers':2,'modes':['flight'],'currency':'INR'}
STAY={'destination':'Agra','check_in':START.isoformat(),'check_out':END.isoformat(),'travelers':2,'rooms':1,'star_ratings':[4],'currency':'INR'}


class Offers:
    def __init__(self):self.docs={}
    async def insert_one(self,doc):self.docs[doc['_id']]=copy.deepcopy(doc)
    async def find_one(self,query):
        doc=self.docs.get(query['_id'])
        if not doc or doc['owner']!=query['owner'] or doc['kind']!=query['kind'] or doc['expires_at']<=query['expires_at']['$gt']:return None
        return copy.deepcopy(doc)


class FixtureTransport:
    name='Fixture Transport'
    modes={'flight'}
    max_stops=0
    booking_hosts={'inventory.example'}
    def __init__(self):self.calls=[]
    async def is_ready(self):return True
    def supports(self,request):return True
    async def search(self,request):
        self.calls.append(request)
        return [TransportOption(mode='flight',provider=self.name,service_name='Test journey',
            departure_at=str(START)+'T08:00:00Z',arrival_at=str(START)+'T10:00:00Z',duration_minutes=120,
            price={'amount':900.5,'currency':'INR','type':'live'},booking_url='https://inventory.example/flight',retrieved_at=datetime.now(timezone.utc))]


class FixtureHotels:
    name='Fixture Hotels'
    booking_hosts={'inventory.example'}
    async def is_ready(self):return True
    def supports(self,request):return True
    async def search(self,request):
        return [HotelOption(hotel_name='Fixture Heritage Hotel',location='Agra',star_rating=4,provider=self.name,
            nightly_price={'amount':1000.05,'currency':'INR','type':'live'},
            total_price={'amount':5000.25,'currency':'INR','type':'live'},
            booking_url='https://inventory.example/hotel',retrieved_at=datetime.now(timezone.utc))]


@pytest.fixture
def api(monkeypatch):
    db=SimpleNamespace(travel_offers=Offers(),monuments=Monuments(),trip_plans=Collection(),command=AsyncMock(return_value={'ok':1}))
    app.dependency_overrides[get_database]=lambda:db
    app.dependency_overrides[get_users_collection]=lambda:object()
    app.dependency_overrides[get_current_user]=lambda:{'username':'alice'}
    monkeypatch.setattr(search_providers,'transport_providers',[])
    monkeypatch.setattr(search_providers,'hotel_providers',[])
    monkeypatch.setattr(capabilities,'ai_ready',AsyncMock(return_value=False))
    monkeypatch.setattr(planner,'search_destination',lambda _:[])
    yield TestClient(app),db
    app.dependency_overrides.clear()


def test_missing_providers_are_honestly_unavailable(api):
    client,db=api
    for path,body in [('transport',JOURNEY),('hotels',STAY)]:
        response=client.post('/api/v1/travel/'+path+'/search',json=body)
        assert response.status_code==200
        assert response.json()['status']=='unavailable' and response.json()['results']==[]
        assert response.json()['warnings']
    assert client.get('/api/v1/travel/capabilities').json()=={'transport_search':False,'hotel_search':False,'extended_planning':False,'profile_onboarding':True}
    assert not db.travel_offers.docs


@pytest.mark.parametrize('change',[
    {'origin':{'label':' '}},{'modes':[]},{'modes':['taxi']},{'modes':['flight','flight']},
    {'stops':[{'label':str(i)} for i in range(7)]},{'travelers':0},{'travelers':21},
    {'departure_date':(START-timedelta(days=100)).isoformat()},{'return_date':(START-timedelta(days=1)).isoformat()},
    {'departure_date':'2026-2-3'},{'currency':'ZZZ'},{'destination':JOURNEY['origin']},
])
def test_journey_validation(api,change):
    assert api[0].post('/api/v1/travel/transport/search',json={**JOURNEY,**change}).status_code==422


@pytest.mark.parametrize('change',[{'check_out':START.isoformat()},{'rooms':3},{'star_ratings':[0]},{'star_ratings':[4,4]},{'currency':'ZZZ'},{'destination':' '}])
def test_hotel_validation(api,change):
    assert api[0].post('/api/v1/travel/hotels/search',json={**STAY,**change}).status_code==422


def test_search_requires_authentication(api):
    app.dependency_overrides.pop(get_current_user)
    assert api[0].post('/api/v1/travel/transport/search',json=JOURNEY).status_code==401
    assert api[0].post('/api/v1/travel/hotels/search',json=STAY).status_code==401


def test_one_way_international_and_unsupported_stops(api,monkeypatch):
    provider=FixtureTransport()
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    body={**JOURNEY,'destination':{'label':'Tokyo, Japan'},'return_date':None,'stops':[{'label':'Singapore'}]}
    response=api[0].post('/api/v1/travel/transport/search',json=body)
    assert response.json()['status']=='unavailable' and not provider.calls
    provider.max_stops=1
    response=api[0].post('/api/v1/travel/transport/search',json=body)
    assert response.json()['status']=='complete'
    assert provider.calls[0].stops[0].label=='Singapore' and provider.calls[0].return_date is None


def test_partial_results_and_confirmed_price_order(api,monkeypatch):
    provider=FixtureTransport()
    original=provider.search
    async def options(request):
        offer=(await original(request))[0]
        return [offer.model_copy(update={'price':None,'booking_url':None}),offer.model_copy(update={'price':offer.price.model_copy(update={'amount':100,'type':'estimate'})}),offer]
    provider.search=options
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    response=api[0].post('/api/v1/travel/transport/search',json={**JOURNEY,'modes':['flight','train']}).json()
    assert response['status']=='partial' and response['unavailable_modes']==['train']
    assert [o['price']['type'] if o['price'] else None for o in response['results']]==['live','estimate',None]


@pytest.mark.parametrize('bad',['https://attacker.example/offer','javascript:alert(1)','https://user:secret@inventory.example/','https://inventory.example/?api_key=secret'])
def test_provider_bad_urls_never_reach_frontend(api,monkeypatch,bad):
    provider=FixtureTransport();original=provider.search
    async def search(request):
        offer=(await original(request))[0].model_dump();offer['booking_url']=bad;return [offer]
    provider.search=search
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    response=api[0].post('/api/v1/travel/transport/search',json=JOURNEY)
    assert response.json()['results']==[] and 'secret' not in response.text


def test_provider_failure_does_not_leak_details(api,monkeypatch):
    provider=FixtureTransport()
    provider.search=AsyncMock(side_effect=RuntimeError('private-provider-token'))
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    response=api[0].post('/api/v1/travel/transport/search',json=JOURNEY)
    assert response.json()['status']=='unavailable' and 'private-provider-token' not in response.text


def test_selected_offers_are_private_and_expire(api,monkeypatch):
    client,db=api
    monkeypatch.setattr(search_providers,'transport_providers',[FixtureTransport()])
    offer=client.post('/api/v1/travel/transport/search',json=JOURNEY).json()['results'][0]
    body={'destination':'Agra','origin':JOURNEY['origin'],'departure_date':JOURNEY['departure_date'],
          'return_date':JOURNEY['return_date'],'travelers':2,'selected_transport_id':offer['id']}
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=draft()))
    app.dependency_overrides[get_current_user]=lambda:{'username':'bob'}
    assert client.post('/api/v1/travel/plans',json=body).status_code==404
    app.dependency_overrides[get_current_user]=lambda:{'username':'alice'}
    assert client.post('/api/v1/travel/plans',json={**body,'travelers':3}).status_code==409
    db.travel_offers.docs[offer['id']]['expires_at']=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert client.post('/api/v1/travel/plans',json=body).status_code==404


def test_confirmed_costs_saved_plan_and_extended_pdf(api,monkeypatch):
    client,db=api
    monkeypatch.setattr(search_providers,'transport_providers',[FixtureTransport()])
    monkeypatch.setattr(search_providers,'hotel_providers',[FixtureHotels()])
    transport=client.post('/api/v1/travel/transport/search',json=JOURNEY).json()['results'][0]
    hotel=client.post('/api/v1/travel/hotels/search',json=STAY).json()['results'][0]
    model=draft(5)
    for day in model.days:day.accommodation_cost=1000
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=model))
    body={'destination':'Agra','days':5,'travelers':2,'budget':9000,'origin':JOURNEY['origin'],
        'departure_date':JOURNEY['departure_date'],'return_date':JOURNEY['return_date'],'travel_mode':'flight',
        'hotel_preferences':{'rooms':1,'star_ratings':[4]},'selected_transport_id':transport['id'],'selected_hotel_id':hotel['id']}
    response=client.post('/api/v1/travel/plans',json=body)
    assert response.status_code==201,response.text
    plan=response.json()
    assert plan['estimated_total']==9400.75 and plan['budget_status']=='over_budget'
    assert plan['cost_summary']['confirmed_total']==5900.75 and plan['cost_summary']['itinerary_estimate']==3500
    assert all(day['accommodation_cost']==0 for day in plan['days'])
    path='/api/v1/travel/plans/'+plan['plan_id']
    assert client.get(path).json()==plan
    pdf=client.get(path+'/pdf')
    text=' '.join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
    assert 'Bengaluru' in text and START.isoformat() in text and 'Fixture Hotels' in text and 'Confirmed provider quote total' in text
    app.dependency_overrides[get_current_user]=lambda:{'username':'bob'}
    assert client.get(path).status_code==404 and client.get(path+'/pdf').status_code==404


def test_extended_plan_without_inventory_uses_estimates_and_keeps_route(api,monkeypatch):
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=draft()))
    response=api[0].post('/api/v1/travel/plans',json={'destination':'Agra','origin':JOURNEY['origin'],
        'departure_date':START.isoformat(),'travel_mode':'bus','hotel_preferences':{'rooms':1},'stops':[{'label':'Hyderabad'}]})
    assert response.status_code==201,response.text
    assert response.json()['estimated_total']==700 and response.json()['cost_summary']['confirmed_total']==0
    assert response.json()['cost_summary']['costs_complete'] is False
    context=planner.generate_json.call_args.args[2]
    assert context['request']['stops'][0]['label']=='Hyderabad'
    assert any('No verified' in text for text in response.json()['warnings'])



def test_capabilities_require_actual_readiness_checks(api,monkeypatch):
    provider=FixtureTransport()
    provider.is_ready=AsyncMock(return_value=False)
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    assert api[0].get('/api/v1/travel/capabilities').json()['transport_search'] is False
    provider.is_ready=AsyncMock(return_value=True)
    assert api[0].get('/api/v1/travel/capabilities').json()['transport_search'] is True
    provider.is_ready.assert_awaited()


def test_ai_capability_does_not_trust_key_presence(monkeypatch):
    from virasatguide_ai_agent.providers import GenerationError
    monkeypatch.setenv('GEMINI_API_KEY','not-a-real-key')
    monkeypatch.setattr(capabilities,'_ai_probe',{'config':None,'checked':0.0,'ready':False})
    probe=AsyncMock(side_effect=GenerationError('unavailable'))
    monkeypatch.setattr(capabilities,'generate',probe)
    assert asyncio.run(capabilities.ai_ready()) is False
    probe.assert_awaited_once()
    monkeypatch.setattr(capabilities,'_ai_probe',{'config':None,'checked':0.0,'ready':False})
    monkeypatch.setattr(capabilities,'generate',AsyncMock(return_value='READY'))
    assert asyncio.run(capabilities.ai_ready()) is True


def test_missing_hotel_total_stays_unknown(api,monkeypatch):
    hotel_provider=FixtureHotels();original=hotel_provider.search
    async def no_price(request):
        offer=(await original(request))[0]
        return [offer.model_copy(update={'nightly_price':None,'total_price':None,'booking_url':None})]
    hotel_provider.search=no_price
    monkeypatch.setattr(search_providers,'hotel_providers',[hotel_provider])
    found=api[0].post('/api/v1/travel/hotels/search',json=STAY).json()
    assert found['status']=='partial' and found['results'][0]['total_price'] is None
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=draft()))
    response=api[0].post('/api/v1/travel/plans',json={'destination':'Agra','travelers':2,
        'departure_date':START.isoformat(),'return_date':END.isoformat(),'selected_hotel_id':found['results'][0]['id']})
    assert response.status_code==201,response.text
    assert response.json()['cost_summary']['confirmed_total']==0
    assert response.json()['cost_summary']['costs_complete'] is False


def test_provider_estimate_is_not_counted_as_confirmed(api,monkeypatch):
    provider=FixtureTransport();original=provider.search
    async def estimated(request):
        offer=(await original(request))[0]
        return [offer.model_copy(update={'price':offer.price.model_copy(update={'type':'estimate'})})]
    provider.search=estimated
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    offer=api[0].post('/api/v1/travel/transport/search',json=JOURNEY).json()['results'][0]
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=draft()))
    response=api[0].post('/api/v1/travel/plans',json={'destination':'Agra','origin':JOURNEY['origin'],
        'travelers':2,'departure_date':START.isoformat(),'return_date':END.isoformat(),'selected_transport_id':offer['id']})
    assert response.status_code==201,response.text
    assert response.json()['cost_summary']['confirmed_total']==0
    assert response.json()['cost_summary']['selected_estimate_total']==900.5


def test_model_instance_cannot_bypass_url_validation(api,monkeypatch):
    provider=FixtureTransport();original=provider.search
    async def unsafe(request):
        offer=(await original(request))[0]
        return [offer.model_copy(update={'booking_url':'https://inventory.example/?api_key=private'})]
    provider.search=unsafe
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    response=api[0].post('/api/v1/travel/transport/search',json=JOURNEY)
    assert response.json()['results']==[] and 'private' not in response.text



def test_multi_mode_search_passes_only_supported_modes_to_adapter(api,monkeypatch):
    provider=FixtureTransport()
    provider.supports=lambda request:request.modes==['flight']
    monkeypatch.setattr(search_providers,'transport_providers',[provider])
    response=api[0].post('/api/v1/travel/transport/search',json={**JOURNEY,'modes':['flight','train']})
    assert response.json()['status']=='partial' and response.json()['unavailable_modes']==['train']
    assert provider.calls[0].modes==['flight']


def test_saved_dated_plans_remain_readable_after_dates_pass(api,monkeypatch):
    client,db=api
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=draft()))
    response=client.post('/api/v1/travel/plans',json={'destination':'Agra','departure_date':START.isoformat()})
    assert response.status_code==201,response.text
    plan_id=response.json()['plan_id']
    db.trip_plans.docs[plan_id]['result']['request']['departure_date']='2000-01-01'
    assert client.get('/api/v1/travel/plans/'+plan_id).status_code==200
    assert client.get('/api/v1/travel/plans/'+plan_id+'/pdf').status_code==200


def test_same_day_trip_does_not_request_a_zero_night_hotel(api,monkeypatch):
    monkeypatch.setattr(planner,'generate_json',AsyncMock(return_value=draft()))
    response=api[0].post('/api/v1/travel/plans',json={'destination':'Agra','departure_date':START.isoformat(),
        'return_date':START.isoformat(),'hotel_preferences':{'rooms':1}})
    assert response.status_code==201,response.text
    assert any('No overnight stay' in text for text in response.json()['warnings'])
