import requests

from flask import request, jsonify
from bootstrap import create_app

from models import Beer, Donut
from stats import DonutStats

# import tracing functions
from ddtrace import tracer
from ddtrace.contrib.flask import TraceMiddleware

# initialize Flask application
app = create_app()

# trace the Flask application
TraceMiddleware(app, tracer, service="match-maker")


# some simple routes
@app.route('/ping')
def ping():
    """
    A health check
    """
    return "200 OK"


@app.route('/beers')
def beers():
    """
    List all beers
    """
    # Get beers from the database
    return jsonify(beers=[b.serialize() for b in Beer.query.all()])


@app.route('/donuts')
def donuts():
    """
    List all donuts
    """
    return jsonify(donuts=[d.serialize() for d in Donut.query.all()])


@app.route('/beers/<name>')
def beer(name):
    """
    Get a beer by name
    """
    return jsonify(Beer.query.filter_by(name=name).first().serialize())


@app.route('/donuts/<name>')
def donut(name):
    """
    Get a donut by name
    """
    return jsonify(Donut.query.filter_by(name=name).first().serialize())


@app.route('/pair/beer')
def pair():
    """A complex endpoint that makes a request to another Python service"""
    name = request.args.get('name')

    with tracer.trace("beer.query", service="beer-database"):
        beer = Beer.query.filter_by(name=name).first()

    # force a query
    with tracer.trace("donuts.query", service="beer-database"):
        Donut.query.all()

    with tracer.trace("donuts.query") as span:
        span.set_tag('beer.name', name)
        match = best_match(beer)
    return jsonify(match=match)


@tracer.wrap()
def get_candidates(beer):
    """
    returns a list of donuts based on hops level of beer
    """
    span = tracer.current_span()
    span.set_tags({'beer.name': beer.name, 'beer.hops': beer.hops})

    db = DonutStats.instance()

    # find our optimal sugar level Donuts above or below this level
    # will certainly not be a good match
    optimal_sugar_level = db.get_optimal_sugar_level(beer.hops)
    return db.get_by_sugar_level(optimal_sugar_level, limit=10)


def best_match(beer):
    """
    returns a single donut matched to the hops level of a beer
    """
    # get a list of donuts that match sugar content for beer
    candidates = get_candidates(beer)

    try:
        # propagate the trace context between the two services
        span = tracer.current_span()
        headers = {
            "X-Datadog-Trace-Id": str(span.trace_id),
            "X-Datadog-Parent-Id": str(span.span_id),
        }

        resp = requests.get(
            "http://taster:5001/taste",
            params={"beer": beer.name, "donuts": candidates},
            timeout=2,
            headers=headers,
        )
    except requests.exceptions.Timeout:
        # log the error
        return "not available"

    return resp.json()["candidate"]
