import { useEffect, useRef } from "react";
import useAsyncRetry from "react-use/lib/useAsyncRetry";
import usePreviousDistinct from "react-use/lib/usePreviousDistinct";
import { ExternalResource, ExternalResourceState, Run } from "@sematic/common/src/Models";
import { useHttpClient } from "src/hooks/httpHooks";

export const TERMINATE_STATE: ExternalResourceState = 'DEACTIVATED';
const POLL_EXTERNAL_RESOURCE_INTERVAL = 1000;

export function useExternalResource(run: Run) {
    const {fetch} = useHttpClient();

    const {value, loading, retry, error} = useAsyncRetry(async ()=> {
        const response = await fetch({
            url: `/api/v1/runs/${run.id}/external_resources`
        });

        const payload = await response.json();

        if (!payload['content']) {
            throw Error('external_resources response is not in the correct format.')
        }

        return payload['content'] as Array<ExternalResource>
    }, [fetch, run.id]);

    const timerHandler = useRef<number>();
    const prevLoading = usePreviousDistinct(loading);
    useEffect(() => {
        // monitors when loading changes from `true` to `false`
        if (prevLoading && !loading) {
            if (!!value && (value.length === 0 || value[0].resource_state !== TERMINATE_STATE)) {
                timerHandler.current = window.setTimeout(retry, POLL_EXTERNAL_RESOURCE_INTERVAL);
            }
        }

        return () => {
            // clean up
            if (timerHandler.current) {
                clearTimeout(timerHandler.current);
            }
        }
    }, [timerHandler, prevLoading, loading, value, retry]);

    return {value, loading, error};
}
