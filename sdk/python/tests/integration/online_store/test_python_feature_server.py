import json
from typing import List

import pytest
from fastapi.testclient import TestClient

from feast.errors import PushSourceNotFoundException
from feast.feast_object import FeastObject
from feast.feature_server import get_app
from feast.utils import _utc_now
from tests.integration.feature_repos.repo_configuration import (
    construct_universal_feature_views,
)
from tests.integration.feature_repos.universal.entities import (
    customer,
    driver,
    location,
)


@pytest.mark.integration
@pytest.mark.universal_online_stores
async def test_get_online_features(python_fs_client_w_fs):
    python_fs_client, fs = python_fs_client_w_fs
    await fs.initialize()

    request_data_dict = {
        "features": [
            "driver_stats:conv_rate",
            "driver_stats:acc_rate",
            "driver_stats:avg_daily_trips",
        ],
        "entities": {"driver_id": [5001, 5002]},
    }
    response = python_fs_client.post(
        "/get-online-features", data=json.dumps(request_data_dict)
    )

    # Check entities and features are present
    parsed_response = json.loads(response.text)
    assert "metadata" in parsed_response
    metadata = parsed_response["metadata"]
    expected_features = ["driver_id", "conv_rate", "acc_rate", "avg_daily_trips"]
    response_feature_names = metadata["feature_names"]
    assert len(response_feature_names) == len(expected_features)
    for expected_feature in expected_features:
        assert expected_feature in response_feature_names
    assert "results" in parsed_response
    results = parsed_response["results"]
    for result in results:
        # Same order as in metadata
        assert len(result["statuses"]) == 2  # Requested two entities
        for status in result["statuses"]:
            assert status == "PRESENT"
    results_driver_id_index = response_feature_names.index("driver_id")
    assert (
        results[results_driver_id_index]["values"]
        == request_data_dict["entities"]["driver_id"]
    )

    await fs.close()


@pytest.mark.integration
@pytest.mark.universal_online_stores
async def test_push(python_fs_client_w_fs):
    python_fs_client, fs = python_fs_client_w_fs

    await fs.initialize()
    initial_temp = await _get_temperatures_from_feature_server(
        python_fs_client, location_ids=[1]
    )
    json_data = json.dumps(
        {
            "push_source_name": "location_stats_push_source",
            "df": {
                "location_id": [1],
                "temperature": [initial_temp[0] * 100],
                "event_timestamp": [str(_utc_now())],
                "created": [str(_utc_now())],
            },
        }
    )
    response = python_fs_client.post(
        "/push",
        data=json_data,
    )

    # Check new pushed temperature is fetched
    assert response.status_code == 200
    actual = await _get_temperatures_from_feature_server(
        python_fs_client, location_ids=[1]
    )
    assert actual == [initial_temp[0] * 100]
    await fs.close()


@pytest.mark.integration
@pytest.mark.universal_online_stores
def test_push_source_does_not_exist(python_fs_client_w_fs):
    python_fs_client, _ = python_fs_client_w_fs

    with pytest.raises(
        PushSourceNotFoundException,
        match="Unable to find push source 'push_source_does_not_exist'",
    ):
        python_fs_client.post(
            "/push",
            data=json.dumps(
                {
                    "push_source_name": "push_source_does_not_exist",
                    "df": {
                        "location_id": [1],
                        "temperature": [100],
                        "event_timestamp": [str(_utc_now())],
                        "created": [str(_utc_now())],
                    },
                }
            ),
        )


async def _get_temperatures_from_feature_server(client, location_ids: List[int]):
    get_request_data = {
        "features": ["pushable_location_stats:temperature"],
        "entities": {"location_id": location_ids},
    }
    response = client.post("/get-online-features", data=json.dumps(get_request_data))
    parsed_response = json.loads(response.text)
    assert "metadata" in parsed_response
    metadata = parsed_response["metadata"]
    response_feature_names = metadata["feature_names"]
    assert "results" in parsed_response
    results = parsed_response["results"]
    results_temperature_index = response_feature_names.index("temperature")
    return results[results_temperature_index]["values"]


@pytest.fixture
def python_fs_client_w_fs(environment, universal_data_sources, request):
    fs = environment.feature_store
    entities, datasets, data_sources = universal_data_sources
    feature_views = construct_universal_feature_views(data_sources)
    feast_objects: List[FeastObject] = []
    feast_objects.extend(feature_views.values())
    feast_objects.extend([driver(), customer(), location()])
    fs.apply(feast_objects)
    fs.materialize(environment.start_date, environment.end_date)
    with TestClient(get_app(fs)) as client:
        yield client, fs
