USE [HlsKeyControl];
GO

ALTER TABLE dbo.Operations
DROP CONSTRAINT CK_Operations_Type;
GO

ALTER TABLE dbo.Operations
ADD CONSTRAINT CK_Operations_Type
CHECK (
    operation_type IN (
        'meter_registered',
        'meter_found',
        'hls_reserved',
        'hls_reset',
        'hls_activated',
        'hls_write_unconfirmed',
        'hls_write_rejected'
    )
);
GO