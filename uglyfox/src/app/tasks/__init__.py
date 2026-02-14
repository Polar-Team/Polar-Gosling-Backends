"""Celery tasks for UglyFox backend.

UglyFox tasks are triggered by cloud triggers (Yandex Cloud Timer Trigger / AWS EventBridge Scheduler)
and handle runner lifecycle management, health monitoring, and pruning.
"""
