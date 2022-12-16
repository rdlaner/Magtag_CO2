def retry(attempts, exceptions):
    """
    Retry Decorator
    Retries the wrapped function/method `attempts` attempts if the exceptions listed
    in ``exceptions`` are thrown
    :param attempts: The number of attempts to repeat the wrapped function/method
    :type attempts: Int
    :param Exceptions: Lists of exceptions that trigger a retry attempt
    :type Exceptions: Tuple of Exceptions
    """
    def decorator(func):
        def newfn(*args, **kwargs):
            attempt = 0
            while attempt < attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions:
                    print(
                        'Exception thrown when attempting to run %s, attempt '
                        '%d of %d' % (func, attempt, attempts)
                    )
                    attempt += 1
        return newfn
    return decorator
