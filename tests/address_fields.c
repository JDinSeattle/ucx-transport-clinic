/* Internal-API regression test tied deliberately to upstream.lock.json. */
#include <ucp/core/ucp_worker.h>
#include <ucp/wireup/address.h>
#include <ucs/sys/mem.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(c) do { if (!(c)) {fprintf(stderr,"check failed: %s\n",#c);return 1;} } while(0)
int main(void) {
    ucp_config_t *config; ucp_context_h ctx; ucp_worker_h worker;
    CHECK(ucp_config_read(NULL,NULL,&config)==UCS_OK);
    CHECK(ucp_config_modify(config,"ADDRESS_DEBUG_INFO","n")==UCS_OK);
    ucp_params_t cp={.field_mask=UCP_PARAM_FIELD_FEATURES,.features=UCP_FEATURE_TAG};
    CHECK(ucp_init(&cp,config,&ctx)==UCS_OK);ucp_config_release(config);
    ucp_worker_params_t wp={.field_mask=UCP_WORKER_PARAM_FIELD_THREAD_MODE|UCP_WORKER_PARAM_FIELD_CLIENT_ID,
                           .thread_mode=UCS_THREAD_MODE_SINGLE,.client_id=UINT64_C(0xfedcba9876543210)};
    CHECK(ucp_worker_create(ctx,&wp,&worker)==UCS_OK);
    worker->uuid=UINT64_C(0x0123456789abcdef);
    ucp_tl_bitmap_t bitmap=UCS_STATIC_BITMAP_ZERO_INITIALIZER;
    unsigned checks=0;
    for(int version=UCP_OBJECT_VERSION_V1;version<=UCP_OBJECT_VERSION_V2;version++) {
        for(unsigned fields=0;fields<4;fields++) {
            unsigned flags=((fields&1)?UCP_ADDRESS_PACK_FLAG_WORKER_UUID:0) |
                           ((fields&2)?UCP_ADDRESS_PACK_FLAG_CLIENT_ID:0);
            void *packed;size_t size;
            CHECK(ucp_address_pack(worker,NULL,&bitmap,flags,version,NULL,1,&size,&packed)==UCS_OK);
            printf("version=%d fields=%u bytes=",version,fields);
            for(size_t i=0;i<size;i++) printf("%02x",((unsigned char*)packed)[i]);
            printf("\n");
            for(unsigned offset=0;offset<16;offset++) {
                unsigned char *storage=malloc(size+16);CHECK(storage!=NULL);
                memset(storage,0xa5,size+16);memcpy(storage+offset,packed,size);
                CHECK(ucp_address_get_uuid(storage+offset)==((fields&1)?worker->uuid:0));
                CHECK(ucp_address_get_client_id(storage+offset)==((fields&2)?worker->client_id:0));
                CHECK(memcmp(storage+offset,packed,size)==0);
                free(storage);checks++;
            }
            ucs_free(packed);
        }
    }
    ucp_worker_destroy(worker);ucp_cleanup(ctx);
    printf("checks=%u result=pass\n",checks);return 0;
}
