# File: tests/server/test_methods.py

import pytest
from pydantic import ValidationError
from a2a.json_rpc.protocol import JSONRPCProtocol
from a2a.server.methods import register_methods
from a2a.server.tasks.task_manager import TaskManager, TaskNotFound
from a2a.server.pubsub import EventBus
from a2a.json_rpc.spec import TextPart, Message, Role, TaskState
from a2a.server.tasks.handlers.echo_handler import EchoHandler


@pytest.fixture
def protocol_manager():
    """
    Set up a fresh EventBus, TaskManager, and JSONRPCProtocol
    with registered A2A methods.
    """
    event_bus = EventBus()
    manager = TaskManager(event_bus)
    # Register the Echo handler as the default
    manager.register_handler(EchoHandler(), default=True)
    
    protocol = JSONRPCProtocol()
    register_methods(protocol, manager)
    return protocol, manager


@pytest.mark.asyncio
async def test_send_and_get(protocol_manager):
    protocol, manager = protocol_manager
    # Valid send params
    params = {
        "id": "ignored",
        "sessionId": None,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": "Hello Methods"}]
        }
    }
    send_handler = protocol._methods["tasks/send"]
    result = await send_handler("tasks/send", params)
    # Alias keys present
    assert isinstance(result.get("id"), str)
    assert isinstance(result.get("sessionId"), str)
    # Enum returned for state
    assert result["status"]["state"] == TaskState.submitted

    # Get the same task
    task_id = result["id"]
    get_handler = protocol._methods["tasks/get"]
    get_result = await get_handler("tasks/get", {"id": task_id})
    assert get_result["id"] == task_id
    assert get_result["status"]["state"] == TaskState.submitted
    
    # Wait for task to complete
    import asyncio
    await asyncio.sleep(1.5)  # Give echo handler time to complete
    
    # Get again to confirm completed
    get_result = await get_handler("tasks/get", {"id": task_id})
    assert get_result["status"]["state"] == TaskState.completed
    
    # Check that artifact was created
    assert get_result.get("artifacts")
    assert len(get_result["artifacts"]) == 1
    assert get_result["artifacts"][0]["name"] == "echo"
    assert get_result["artifacts"][0]["parts"][0]["text"] == "Echo: Hello Methods"


@pytest.mark.asyncio
async def test_send_invalid_params(protocol_manager):
    protocol, _ = protocol_manager
    send_handler = protocol._methods["tasks/send"]
    # Missing required 'message'
    with pytest.raises(ValidationError):
        await send_handler("tasks/send", {"id": "ignored", "sessionId": None})


@pytest.mark.asyncio
async def test_cancel(protocol_manager):
    protocol, manager = protocol_manager
    # Create then cancel a task
    send_res = await protocol._methods["tasks/send"](
        "tasks/send",
        {"id": "ignored", "sessionId": None, "message": {"role": "user", "parts": [{"type": "text", "text": "To be canceled"}]}}
    )
    task_id = send_res["id"]

    # Give it a moment to start processing
    import asyncio
    await asyncio.sleep(0.1)
    
    cancel_handler = protocol._methods["tasks/cancel"]
    cancel_res = await cancel_handler("tasks/cancel", {"id": task_id})
    assert cancel_res is None
    
    # Wait for cancellation to take effect
    await asyncio.sleep(0.5)
    
    # Manager should reflect canceled state
    task = await manager.get_task(task_id)
    assert task.status.state == TaskState.canceled


@pytest.mark.asyncio
async def test_cancel_nonexistent(protocol_manager):
    protocol, _ = protocol_manager
    cancel_handler = protocol._methods["tasks/cancel"]
    with pytest.raises(TaskNotFound):
        await cancel_handler("tasks/cancel", {"id": "nonexistent"})


@pytest.mark.asyncio
async def test_send_subscribe_and_resubscribe(protocol_manager):
    protocol, manager = protocol_manager
    # sendSubscribe works like send but with handler selection
    sub_res = await protocol._methods["tasks/sendSubscribe"](
        "tasks/sendSubscribe",
        {
            "id": "ignored", 
            "sessionId": None, 
            "message": {"role": "user", "parts": [{"type": "text", "text": "Sub me"}]},
            "handler": "echo"  # Explicitly specify handler
        }
    )
    assert isinstance(sub_res.get("id"), str)
    assert sub_res["status"]["state"] == TaskState.submitted

    # resubscribe is a no-op stub
    resub_res = await protocol._methods["tasks/resubscribe"](
        "tasks/resubscribe", {"id": sub_res["id"]}
    )
    assert resub_res is None
    
    # Wait a moment for task to process
    import asyncio
    await asyncio.sleep(1.5)
    
    # Get the task to confirm it was processed
    get_handler = protocol._methods["tasks/get"]
    get_result = await get_handler("tasks/get", {"id": sub_res["id"]})
    assert get_result["status"]["state"] == TaskState.completed
    assert get_result.get("artifacts")
    assert get_result["artifacts"][0]["parts"][0]["text"] == "Echo: Sub me"


@pytest.mark.asyncio
async def test_handler_selection(protocol_manager):
    """Test that we can select different handlers."""
    protocol, manager = protocol_manager
    
    # Create a second handler just for this test
    class TestHandler(EchoHandler):
        @property
        def name(self) -> str:
            return "test"
        
        async def process_task(self, task_id, message, session_id=None):
            # Just yield a completion status directly
            from a2a.json_rpc.spec import TaskStatusUpdateEvent, TaskStatus
            yield TaskStatusUpdateEvent(
                id=task_id,
                status=TaskStatus(state=TaskState.completed),
                final=True
            )
    
    # Register this handler
    manager.register_handler(TestHandler())
    
    # Create a task with this handler
    send_res = await protocol._methods["tasks/sendSubscribe"](
        "tasks/sendSubscribe",
        {
            "id": "ignored", 
            "sessionId": None, 
            "message": {"role": "user", "parts": [{"type": "text", "text": "Test Handler"}]},
            "handler": "test"  # Specify our test handler
        }
    )
    
    task_id = send_res["id"]
    
    # Wait for task to complete
    import asyncio
    await asyncio.sleep(0.5)
    
    # Verify it was processed with the test handler (no artifact, just completion)
    get_handler = protocol._methods["tasks/get"]
    get_result = await get_handler("tasks/get", {"id": task_id})
    assert get_result["status"]["state"] == TaskState.completed
    assert not get_result.get("artifacts")  # No artifacts with test handler