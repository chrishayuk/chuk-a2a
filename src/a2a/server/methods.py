# File: a2a/server/methods.py
import asyncio
import logging

from a2a.json_rpc.spec import (
    TaskSendParams,
    TaskQueryParams,
    TaskIdParams,
    Task,
    TaskState,
)
from a2a.json_rpc.protocol import JSONRPCProtocol
from a2a.server.tasks.task_manager import TaskManager

# Configure module logger
logger = logging.getLogger(__name__)

# Keep track of active background tasks
_background_tasks = set()

def _register_task(task):
    """Register a background task for cleanup."""
    _background_tasks.add(task)
    
    # Set up removal when the task is done
    def _clean_task(t):
        _background_tasks.discard(t)
    task.add_done_callback(_clean_task)
    
    return task

async def cancel_pending_tasks():
    """Cancel all pending background tasks and wait for them to complete."""
    tasks = list(_background_tasks)
    num_tasks = len(tasks)
    
    if num_tasks > 0:
        logger.info(f"Cancelling {num_tasks} pending tasks")
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait for all tasks to complete cancellation
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.debug("All tasks cancelled successfully")
    
    _background_tasks.clear()


def register_methods(
    protocol: JSONRPCProtocol,
    manager: TaskManager,
) -> None:
    """
    Register JSON-RPC methods for task operations.
    """
    @protocol.method("tasks/send")
    async def _send(method: str, params: dict) -> dict:
        logger.info(f"Received RPC method {method}")
        logger.debug(f"Method params: {params}")
        p = TaskSendParams.model_validate(params)
        
        # Create task with the default handler
        task = await manager.create_task(p.message, session_id=p.session_id)
        logger.info(f"Created task {task.id} via {method}")
        
        # Return using alias keys
        result = Task.model_validate(task.model_dump()).model_dump(exclude_none=True, by_alias=True)
        logger.debug(f"tasks/send returning: {result}")
        return result

    @protocol.method("tasks/get")
    async def _get(method: str, params: dict) -> dict:
        logger.info(f"Received RPC method {method}")
        logger.debug(f"Method params: {params}")
        q = TaskQueryParams.model_validate(params)
        task = await manager.get_task(q.id)
        logger.debug(f"Retrieved task {q.id}: state={task.status.state}")
        
        result = Task.model_validate(task.model_dump()).model_dump(exclude_none=True, by_alias=True)
        logger.debug(f"tasks/get returning: {result}")
        return result

    @protocol.method("tasks/cancel")
    async def _cancel(method: str, params: dict) -> None:
        logger.info(f"Received RPC method {method}")
        logger.debug(f"Method params: {params}")
        iid = TaskIdParams.model_validate(params)
        await manager.cancel_task(iid.id)
        logger.info(f"Task {iid.id} canceled via RPC")
        return None

    @protocol.method("tasks/sendSubscribe")
    async def _send_subscribe(method: str, params: dict) -> dict:
        logger.info(f"Received RPC method {method}")
        logger.debug(f"Method params: {params}")
        p = TaskSendParams.model_validate(params)
        
        # Create task with specified or default handler
        handler_name = params.get("handler")  # Optional handler selection
        handler_info = f" using handler '{handler_name}'" if handler_name else ""
        
        task = await manager.create_task(p.message, session_id=p.session_id, handler_name=handler_name)
        logger.info(f"Created task {task.id} via {method}{handler_info}")
        
        result = Task.model_validate(task.model_dump()).model_dump(exclude_none=True, by_alias=True)
        logger.debug(f"tasks/sendSubscribe returning: {result}")
        return result

    @protocol.method("tasks/resubscribe")
    async def _resubscribe(method: str, params: dict) -> None:
        # no-op: the SSE sidecar handles resubscribe by replaying events
        logger.info(f"Received RPC method {method} (resubscribe)")
        logger.debug(f"Resubscribe params: {params}")
        return None
        
    # Add a cleanup method to the protocol
    protocol.cancel_pending_tasks = cancel_pending_tasks