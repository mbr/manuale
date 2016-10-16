"""
The domain authorization command.
"""

import logging
import time
import hashlib
import os

from .acme import Acme
from .crypto import generate_jwk_thumbprint, jose_b64
from .errors import ManualeError, AcmeError
from .helpers import confirm

logger = logging.getLogger(__name__)


def get_challenge(auth, auth_type):
    try:
        return [ch
                for ch in auth.get('challenges', [])
                if ch.get('type') == auth_type][0]
    except IndexError:
        raise ManualeError(
            "Cannot find {} challenge. The server did not return one.".format(
                auth_type))


def retrieve_verification(acme, domain, auth):
    while True:
        logger.info(
            "{}: waiting for verification. Checking in 5 seconds.".format(
                domain))
        time.sleep(5)

        response = acme.get_authorization(auth['uri'])
        status = response.get('status')
        if status == 'valid':
            logger.info("{}: OK! Authorization lasts until {}.".format(
                domain, response.get('expires', '(not provided)')))
            return True
        elif status != 'pending':
            # Failed, dig up details
            error_type, error_reason = "unknown", "N/A"
            try:
                challenge = [
                    ch
                    for ch in response.get('challenges', [])
                    if ch.get('type') == 'dns-01'
                ][0]
                error_type = challenge.get('error').get('type')
                error_reason = challenge.get('error').get('detail')
            except (ValueError, IndexError, AttributeError, TypeError):
                pass

            logger.info("{}: {} ({})".format(domain, error_reason, error_type))
            return False


def authorize(server, account, domains, method='dns'):
    acme = Acme(server, account)
    thumbprint = generate_jwk_thumbprint(account.key)

    try:
        # Get pending authorizations for each domain
        authz = {}
        for domain in domains:
            logger.info("Requesting challenge for {}.".format(domain))
            created = acme.new_authorization(domain)
            auth = created.contents
            auth['uri'] = created.uri

            # Check if domain is already authorized
            if auth.get('status') == 'valid':
                logger.info("{} is already authorized until {}.".format(
                    domain, auth.get('expires', '(unknown)')))
                continue

            # Find the DNS challenge
            auth['challenge'] = get_challenge(auth, method + '-01')

            auth['key_authorization'] = "{}.{}".format(
                auth['challenge'].get('token'), thumbprint)
            digest = hashlib.sha256()
            digest.update(auth['key_authorization'].encode('ascii'))
            auth['txt_record'] = jose_b64(digest.digest())

            authz[domain] = auth

        # Quit if nothing to authorize
        if not authz:
            logger.info("")
            logger.info("All domains are already authorized, exiting.")
            return

        done, failed = set(), set()
        if method == 'dns':
            logger.info("")
            logger.info(
                "DNS verification required. Make sure these TXT records are in place:")
            logger.info("")
            for domain, auth in authz.items():
                logger.info("  _acme-challenge.{}.  IN TXT  \"{}\"".format(
                    domain, auth['txt_record']))
            logger.info("")
            input("Press enter to continue.")

            # Verify each domain
            for domain in authz.keys():
                logger.info("")
                auth = authz[domain]
                challenge = auth['challenge']
                acme.validate_authorization(challenge['uri'], 'dns-01',
                                            auth['key_authorization'])

                if retrieve_verification(acme, domain, auth):
                    done.add(domain)
                else:
                    failed.add(domain)

        elif method == 'http':
            logger.info("")
            logger.info(
                "HTTP verification required. Ensure the following URLs are "
                "reachable:")

            for domain, auth in authz.items():
                token = auth['challenge'].get('token')
                auth['key_authorization'] = "{}.{}".format(token, thumbprint)

                # path sanity check
                assert (token and os.path.sep not in token and
                        '.' not in token)
                with open(token, 'w') as out:
                    out.write(auth['key_authorization'])

                wkurl = 'http://{}/.well-known/acme-challenge/{}'.format(
                    domain, token)

                logger.info("")
                logger.info(wkurl)
                logger.info("")
                logger.info("The necessary files have been writen to the "
                            "current directory")
                logger.info("")

            input("Press enter to continue.")

            # at this point, we assume the files have been uploaded. try to
            # validate
            for domain, auth in authz.items():
                logger.info("")
                challenge = auth['challenge']
                acme.validate_authorization(challenge['uri'], 'dns-01',
                                            auth['key_authorization'])

                if retrieve_verification(acme, domain, auth):
                    done.add(domain)
                else:
                    failed.add(domain)

        else:
            raise NotImplementedError(
                'Authorization method {!r} not implemented'.format(method))

        logger.info("")
        if failed:
            logger.info("{} domain(s) authorized, {} failed.".format(
                len(done), len(failed)))
            logger.info("Authorized: {}".format(' '.join(done) or "N/A"))
            logger.info("Failed: {}".format(' '.join(failed)))
        else:
            logger.info("{} domain(s) authorized. Let's Encrypt!".format(len(
                done)))

    except IOError as e:
        logger.error("A connection or service error occurred. Aborting.")
        raise ManualeError(e)
